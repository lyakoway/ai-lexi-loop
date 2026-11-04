"""Integration tests for progress persistence and the lesson scenario."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expression, ExpressionProgress, Profile
from app.repositories import (
    ExpressionProgressRepository,
    ProfileRepository,
    StorageError,
    UserRepository,
)
from app.schemas.profile import ProfileCreate
from app.services import ProfileService
from app.services.lessons import LessonPlan, LessonService, LessonUnavailable
from tests.integration.conftest import make_analytics

NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


def profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "native_language": "ru",
        "target_language": "ts",
        "level": "A1",
        "learning_goal": "work",
        "topics": ["everyday"],
        "preferred_accent": "uk",
        "daily_study_minutes": 15,
        "new_items_per_day": 2,
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "Europe/Moscow",
    }
    payload.update(overrides)
    return payload


async def insert_expression(
    db_session: AsyncSession,
    *,
    text_value: str,
    level: str = "A1",
    topic: str = "everyday",
) -> Expression:
    expression = Expression(
        language="ts",
        text=text_value,
        translation="перевод",
        ipa="aɪ piː eɪ",
        phonetic_hint_ru="пОдсказка",
        example="example",
        example_translation="пример",
        level=level,
        topic=topic,
    )
    db_session.add(expression)
    await db_session.flush()
    return expression


async def create_user_with_profile(
    db_session: AsyncSession, **profile_overrides: object
) -> int:
    user, _ = await UserRepository(db_session).get_or_create_by_identity(
        "telegram", "9001"
    )
    await ProfileService(
        ProfileRepository(db_session),
        make_analytics(db_session),
    ).save_profile(
        user.id, ProfileCreate.model_validate(profile_payload(**profile_overrides))
    )
    return user.id


# ---------------------------------------------------------------------------
# Progress repository
# ---------------------------------------------------------------------------


async def test_bulk_create_and_read_served_ids(db_session: AsyncSession) -> None:
    user_id = await create_user_with_profile(db_session)
    first = await insert_expression(db_session, text_value="one")
    second = await insert_expression(db_session, text_value="two")
    repository = ExpressionProgressRepository(db_session)

    await repository.bulk_create(user_id, [first.id, second.id], now=NOW)

    assert await repository.get_served_expression_ids(user_id) == {
        first.id,
        second.id,
    }


async def test_user_expression_pair_is_unique(db_session: AsyncSession) -> None:
    user_id = await create_user_with_profile(db_session)
    expression = await insert_expression(db_session, text_value="one")
    repository = ExpressionProgressRepository(db_session)
    await repository.bulk_create(user_id, [expression.id], now=NOW)

    with pytest.raises(StorageError) as exc_info:
        await repository.bulk_create(user_id, [expression.id], now=NOW)

    assert exc_info.value.conflict is True
    await db_session.rollback()


async def test_progress_requires_real_expression(db_session: AsyncSession) -> None:
    user_id = await create_user_with_profile(db_session)
    repository = ExpressionProgressRepository(db_session)

    with pytest.raises(StorageError):
        await repository.bulk_create(user_id, [999999], now=NOW)
    await db_session.rollback()


async def test_mark_completed_once(migrated_db: None, db_session: AsyncSession) -> None:
    user_id = await create_user_with_profile(db_session)
    first = await insert_expression(db_session, text_value="one")
    second = await insert_expression(db_session, text_value="two")
    repository = ExpressionProgressRepository(db_session)
    await repository.bulk_create(user_id, [first.id, second.id], now=NOW)

    completed_first = await repository.mark_completed(user_id, [first.id])
    completed_again = await repository.mark_completed(user_id, [first.id])
    await db_session.flush()

    rows = {
        row.expression_id: row.completed_at
        for row in (
            await db_session.execute(
                select(ExpressionProgress).where(ExpressionProgress.user_id == user_id)
            )
        )
        .scalars()
        .all()
    }

    assert completed_first == 1
    assert completed_again == 0
    assert rows[first.id] is not None
    assert rows[second.id] is None


# ---------------------------------------------------------------------------
# LessonService end-to-end on real storage
# ---------------------------------------------------------------------------


def make_service(db_session: AsyncSession) -> LessonService:
    from app.repositories import ExpressionRepository

    return LessonService(
        ExpressionRepository(db_session),
        ExpressionProgressRepository(db_session),
        make_analytics(db_session),
    )


async def make_profile_row(db_session: AsyncSession, **overrides: object) -> Profile:
    user_id = await create_user_with_profile(db_session, **overrides)
    result = await db_session.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one()
    assert profile is not None
    return profile


async def test_lesson_respects_limit_and_excludes_served(
    db_session: AsyncSession,
) -> None:
    await insert_expression(db_session, text_value="one")
    await insert_expression(db_session, text_value="two")
    await insert_expression(db_session, text_value="three")
    profile = await make_profile_row(db_session)
    service = make_service(db_session)

    first_lesson = await service.build_lesson(profile.user_id, profile)
    assert isinstance(first_lesson, LessonPlan)
    assert len(first_lesson.expressions) == 2  # new_items_per_day == 2

    second_lesson = await service.build_lesson(profile.user_id, profile)
    assert isinstance(second_lesson, LessonPlan)
    served_ids = {expression.id for expression in first_lesson.expressions}
    assert {expression.id for expression in second_lesson.expressions}.isdisjoint(
        served_ids
    )

    third = await service.build_lesson(profile.user_id, profile)
    assert isinstance(third, LessonUnavailable)
    assert third.reason == "all_served"


async def test_lesson_uses_a2_when_level_unknown(db_session: AsyncSession) -> None:
    await insert_expression(db_session, text_value="a2-one", level="A2")
    profile = await make_profile_row(db_session, level="unknown")
    service = make_service(db_session)

    plan = await service.build_lesson(profile.user_id, profile)

    assert isinstance(plan, LessonPlan)
    assert plan.used_default_level is True
    assert plan.expressions[0].level == "A2"


async def test_lesson_reports_missing_topics_strictly(
    db_session: AsyncSession,
) -> None:
    """Expressions exist, but for other topics: no silent substitution."""
    await insert_expression(db_session, text_value="sport-one", topic="sport")
    profile = await make_profile_row(db_session, topics=["music"])
    service = make_service(db_session)

    result = await service.build_lesson(profile.user_id, profile)

    assert isinstance(result, LessonUnavailable)
    assert result.reason == "no_expressions_for_topics"


async def test_lesson_reports_level_mismatch_strictly(
    db_session: AsyncSession,
) -> None:
    await insert_expression(db_session, text_value="a1-one", level="A1")
    profile = await make_profile_row(db_session, level="C1")
    service = make_service(db_session)

    result = await service.build_lesson(profile.user_id, profile)

    assert isinstance(result, LessonUnavailable)
    assert result.reason == "no_expressions_for_level"


async def test_lesson_inactive_expressions_are_not_served(
    db_session: AsyncSession,
) -> None:
    expression = await insert_expression(db_session, text_value="hidden")
    expression.is_active = False
    await db_session.flush()
    profile = await make_profile_row(db_session)
    service = make_service(db_session)

    result = await service.build_lesson(profile.user_id, profile)

    assert isinstance(result, LessonUnavailable)


async def test_finish_lesson_completes_shown_cards(
    db_session: AsyncSession,
) -> None:
    await insert_expression(db_session, text_value="one")
    await insert_expression(db_session, text_value="two")
    profile = await make_profile_row(db_session)
    service = make_service(db_session)
    plan = await service.build_lesson(profile.user_id, profile)
    assert isinstance(plan, LessonPlan)

    # The user finished the lesson from the first card.
    completed = await service.finish_lesson(profile.user_id, [plan.expressions[0].id])

    assert completed == 1
    rows = {
        row.expression_id: row.completed_at
        for row in (
            await db_session.execute(
                select(ExpressionProgress).where(
                    ExpressionProgress.user_id == profile.user_id
                )
            )
        )
        .scalars()
        .all()
    }
    assert rows[plan.expressions[0].id] is not None
    assert rows[plan.expressions[1].id] is None
