"""Integration tests for spaced repetition on a real PostgreSQL."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expression, ExpressionProgress, Profile
from app.domain.srs import SrsState
from app.repositories import (
    ExpressionProgressRepository,
    ExpressionRepository,
    ProfileRepository,
    UserRepository,
)
from app.schemas.profile import ProfileCreate
from app.services.lessons import LessonPlan, LessonService
from app.services.reviews import ReviewPlan, ReviewService, ReviewUnavailable
from tests.integration.conftest import make_analytics

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
TOMORROW = NOW + timedelta(days=1)
IN_TWO_DAYS = NOW + timedelta(days=2)


async def insert_expression(db_session: AsyncSession, *, text_value: str) -> Expression:
    expression = Expression(
        language="ts",
        text=text_value,
        translation="перевод",
        ipa="aɪ piː eɪ",
        phonetic_hint_ru="пОдсказка",
        example="example",
        example_translation="пример",
        level="A1",
        topic="everyday",
    )
    db_session.add(expression)
    await db_session.flush()
    return expression


async def make_profile(
    db_session: AsyncSession, *, new_items_per_day: int = 5
) -> Profile:
    from app.services import ProfileService

    user, _ = await UserRepository(db_session).get_or_create_by_identity(
        "telegram", "9001"
    )
    await ProfileService(
        ProfileRepository(db_session),
        make_analytics(db_session),
    ).save_profile(
        user.id,
        ProfileCreate.model_validate(
            {
                "native_language": "ru",
                "target_language": "ts",
                "level": "A1",
                "learning_goal": "work",
                "topics": ["everyday"],
                "preferred_accent": "uk",
                "daily_study_minutes": 15,
                "new_items_per_day": new_items_per_day,
                "lesson_time": "08:00",
                "review_time": "19:00",
                "timezone": "Europe/Moscow",
            }
        ),
    )
    result = await db_session.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one()
    assert profile is not None
    return profile


def make_service(db_session: AsyncSession) -> ReviewService:
    return ReviewService(
        ExpressionProgressRepository(db_session),
        ExpressionRepository(db_session),
        make_analytics(db_session),
    )


# ---------------------------------------------------------------------------
# Scheduling on serve
# ---------------------------------------------------------------------------


async def test_served_card_is_scheduled_for_tomorrow(db_session: AsyncSession) -> None:
    """A card from a fresh lesson gets its first review a day later."""
    await insert_expression(db_session, text_value="one")
    await insert_expression(db_session, text_value="two")
    profile = await make_profile(db_session)
    lesson = LessonService(
        ExpressionRepository(db_session),
        ExpressionProgressRepository(db_session),
        make_analytics(db_session),
    )

    plan = await lesson.build_lesson(profile.user_id, profile, now=NOW)

    assert isinstance(plan, LessonPlan)
    rows = list(
        (
            await db_session.execute(
                select(ExpressionProgress).where(
                    ExpressionProgress.user_id == profile.user_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    for row in rows:
        assert row.next_review_at == TOMORROW
        assert row.interval_days == 1
        assert row.review_count == 0
        assert row.ease_factor == 2.5
        assert row.last_reviewed_at is None


async def test_not_due_cards_are_not_served_for_review(
    db_session: AsyncSession,
) -> None:
    await insert_expression(db_session, text_value="one")
    profile = await make_profile(db_session)
    lesson = LessonService(
        ExpressionRepository(db_session),
        ExpressionProgressRepository(db_session),
        make_analytics(db_session),
    )
    await lesson.build_lesson(profile.user_id, profile, now=NOW)

    result_same_day = await make_service(db_session).build_review(
        profile.user_id, now=NOW
    )
    result_next_day = await make_service(db_session).build_review(
        profile.user_id, now=TOMORROW
    )

    assert isinstance(result_same_day, ReviewUnavailable)
    assert isinstance(result_next_day, ReviewPlan)
    assert len(result_next_day.expressions) == 1


# ---------------------------------------------------------------------------
# Due filters
# ---------------------------------------------------------------------------


async def test_due_filters_by_user_order_and_limit(
    db_session: AsyncSession,
) -> None:
    repository = ExpressionProgressRepository(db_session)
    other_user, _ = await UserRepository(db_session).get_or_create_by_identity(
        "telegram", "9002"
    )
    first_user, _ = await UserRepository(db_session).get_or_create_by_identity(
        "telegram", "9001"
    )
    expressions = [
        await insert_expression(db_session, text_value=text_value)
        for text_value in ("one", "two", "three")
    ]
    await repository.bulk_create(first_user.id, [expressions[0].id], now=NOW)
    await repository.bulk_create(
        first_user.id, [expressions[1].id], now=NOW - timedelta(days=3)
    )
    await repository.bulk_create(
        other_user.id, [expressions[2].id], now=NOW - timedelta(days=3)
    )

    due = await repository.get_due(first_user.id, now=NOW, limit=10)

    # Only this user's cards, oldest due first; the not-yet-due card of
    # the same user is excluded, and so is the other user's card.
    assert [row.expression_id for row in due] == [expressions[1].id]
    limited = await repository.get_due(first_user.id, now=NOW, limit=0)
    assert limited == []


# ---------------------------------------------------------------------------
# Applying answers
# ---------------------------------------------------------------------------


async def test_apply_answer_persists_srs_state(db_session: AsyncSession) -> None:
    await insert_expression(db_session, text_value="one")
    profile = await make_profile(db_session)
    service = make_service(db_session)
    lesson = LessonService(
        ExpressionRepository(db_session),
        ExpressionProgressRepository(db_session),
        make_analytics(db_session),
    )
    plan = await lesson.build_lesson(profile.user_id, profile, now=NOW)
    assert isinstance(plan, LessonPlan)
    expression_id = plan.expressions[0].id

    first = await service.apply_answer(
        profile.user_id, expression_id, remembered=True, now=TOMORROW
    )
    second = await service.apply_answer(
        profile.user_id,
        expression_id,
        remembered=True,
        now=first.next_review_at if first else TOMORROW,
    )

    row = await ExpressionProgressRepository(db_session).get_by_pair(
        profile.user_id, expression_id
    )
    assert row is not None
    assert row.review_count == 2
    assert row.interval_days == 6
    assert row.ease_factor == 2.5
    assert row.last_reviewed_at == (second.answered_at if second else None)
    assert row.next_review_at == (second.next_review_at if second else None)


async def test_forgot_answer_resets_and_reappears_next_day(
    db_session: AsyncSession,
) -> None:
    await insert_expression(db_session, text_value="one")
    profile = await make_profile(db_session)
    service = make_service(db_session)
    lesson = LessonService(
        ExpressionRepository(db_session),
        ExpressionProgressRepository(db_session),
        make_analytics(db_session),
    )
    plan = await lesson.build_lesson(profile.user_id, profile, now=NOW)
    assert isinstance(plan, LessonPlan)
    expression_id = plan.expressions[0].id

    await service.apply_answer(
        profile.user_id, expression_id, remembered=True, now=TOMORROW
    )
    forgotten = await service.apply_answer(
        profile.user_id,
        expression_id,
        remembered=False,
        now=IN_TWO_DAYS,
    )

    assert forgotten is not None
    assert forgotten.interval_days == 1
    assert forgotten.ease_factor == 2.0
    next_day = forgotten.next_review_at
    result = await service.build_review(
        profile.user_id, now=next_day + timedelta(hours=1)
    )
    assert isinstance(result, ReviewPlan)
    assert result.expressions[0].id == expression_id


async def test_apply_answer_for_unknown_expression_returns_none(
    db_session: AsyncSession,
) -> None:
    profile = await make_profile(db_session)
    update = await make_service(db_session).apply_answer(
        profile.user_id, 999999, remembered=True, now=NOW
    )
    assert update is None


# ---------------------------------------------------------------------------
# /lesson compatibility
# ---------------------------------------------------------------------------


async def test_reviewed_expression_still_not_served_as_new(
    db_session: AsyncSession,
) -> None:
    """Reviews do not resurrect cards into new lessons."""
    await insert_expression(db_session, text_value="one")
    await insert_expression(db_session, text_value="two")
    profile = await make_profile(db_session, new_items_per_day=1)
    lesson = LessonService(
        ExpressionRepository(db_session),
        ExpressionProgressRepository(db_session),
        make_analytics(db_session),
    )
    service = make_service(db_session)

    first = await lesson.build_lesson(profile.user_id, profile, now=NOW)
    assert isinstance(first, LessonPlan)
    await service.apply_answer(
        profile.user_id,
        first.expressions[0].id,
        remembered=True,
        now=TOMORROW,
    )
    second = await lesson.build_lesson(profile.user_id, profile, now=TOMORROW)

    assert isinstance(second, LessonPlan)
    assert second.expressions[0].id != first.expressions[0].id


async def test_abandoned_lesson_card_returns_via_review(
    db_session: AsyncSession,
) -> None:
    """A card served but never completed still gets reviewed."""
    await insert_expression(db_session, text_value="one")
    profile = await make_profile(db_session)
    lesson = LessonService(
        ExpressionRepository(db_session),
        ExpressionProgressRepository(db_session),
        make_analytics(db_session),
    )
    plan = await lesson.build_lesson(profile.user_id, profile, now=NOW)
    assert isinstance(plan, LessonPlan)
    # No lesson finish happens here — the lesson was abandoned.

    result = await make_service(db_session).build_review(profile.user_id, now=TOMORROW)

    assert isinstance(result, ReviewPlan)
    assert result.expressions[0].id == plan.expressions[0].id


async def test_mark_completed_leaves_srs_state_intact(
    db_session: AsyncSession,
) -> None:
    """Completing a lesson only stamps completed_at."""
    await insert_expression(db_session, text_value="one")
    profile = await make_profile(db_session)
    progress_repository = ExpressionProgressRepository(db_session)
    lesson = LessonService(
        ExpressionRepository(db_session),
        progress_repository,
        make_analytics(db_session),
    )
    plan = await lesson.build_lesson(profile.user_id, profile, now=NOW)
    assert isinstance(plan, LessonPlan)
    expression_id = plan.expressions[0].id
    await make_service(db_session).apply_answer(
        profile.user_id, expression_id, remembered=True, now=TOMORROW
    )

    completed = await progress_repository.mark_completed(
        profile.user_id, [expression_id]
    )

    row = await progress_repository.get_by_pair(profile.user_id, expression_id)
    assert completed == 1
    assert row is not None
    assert row.completed_at is not None
    assert row.review_count == 1  # untouched by mark_completed


def test_srs_state_helper_matches_persisted_defaults() -> None:
    """The in-code initial state equals the DB defaults of migration 0004."""
    state = SrsState()
    assert state.ease_factor == 2.5
    assert state.interval_days == 1
    assert state.review_count == 0
