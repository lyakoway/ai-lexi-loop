"""Integration tests: /settings changes must never touch SRS progress.

The DoD requires an explicit before/after check: changing the level,
the topics or the target language rewrites only ``profiles``; the
``expression_progress`` rows (scheduling state and review history)
stay byte-identical, and the old material remains in the plan.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expression, ExpressionProgress
from app.repositories import ProfileRepository, UserRepository
from app.schemas.profile import ProfileCreate, ProfileUpdate
from app.services.profiles import ProfileService
from tests.integration.conftest import make_analytics

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


def profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "native_language": "ru",
        "target_language": "en",
        "level": "A1",
        "learning_goal": "work",
        "topics": ["everyday"],
        "preferred_accent": "us",
        "daily_study_minutes": 15,
        "new_items_per_day": 5,
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "UTC",
    }
    payload.update(overrides)
    return payload


async def create_user_with_progress(
    db_session: AsyncSession,
) -> tuple[int, list[ExpressionProgress], list[Expression]]:
    """A user with a profile and two served cards in mid-schedule."""

    user, _ = await UserRepository(db_session).get_or_create_by_identity(
        "telegram", "10001"
    )
    await ProfileService(
        ProfileRepository(db_session), make_analytics(db_session)
    ).save_profile(user.id, ProfileCreate.model_validate(profile_payload()))

    expressions = []
    rows = []
    for number, (text_value, topic) in enumerate(
        [("Break a leg", "everyday"), ("Around the clock", "work")]
    ):
        expression = Expression(
            language="en",
            text=text_value,
            translation=f"перевод {number}",
            ipa="breɪk ə leɡ",
            phonetic_hint_ru="брэйк э лэг",
            example="example",
            example_translation="пример",
            level="A1",
            topic=topic,
        )
        db_session.add(expression)
        await db_session.flush()
        row = ExpressionProgress(
            user_id=user.id,
            expression_id=expression.id,
            review_count=3,
            ease_factor=2.5,
            interval_days=15,
            next_review_at=NOW + timedelta(days=15),
            last_reviewed_at=NOW - timedelta(days=1),
            completed_at=NOW - timedelta(days=2) if number == 0 else None,
        )
        db_session.add(row)
        await db_session.flush()
        expressions.append(expression)
        rows.append(row)
    return user.id, rows, expressions


async def snapshot_progress(
    db_session: AsyncSession, user_id: int
) -> list[dict[str, object]]:
    result = await db_session.execute(
        select(ExpressionProgress)
        .where(ExpressionProgress.user_id == user_id)
        .order_by(ExpressionProgress.id)
    )
    return [
        {
            "expression_id": row.expression_id,
            "review_count": row.review_count,
            "ease_factor": row.ease_factor,
            "interval_days": row.interval_days,
            "next_review_at": row.next_review_at,
            "last_reviewed_at": row.last_reviewed_at,
            "completed_at": row.completed_at,
            "shown_at": row.shown_at,
        }
        for row in result.scalars().all()
    ]


async def test_level_change_preserves_srs_history(
    migrated_db: None, db_session: AsyncSession
) -> None:
    user_id, _, _ = await create_user_with_progress(db_session)
    before = await snapshot_progress(db_session, user_id)

    service = ProfileService(ProfileRepository(db_session), make_analytics(db_session))
    updated = await service.update_profile(user_id, ProfileUpdate(level="B2"))

    assert updated.level == "B2"
    after = await snapshot_progress(db_session, user_id)
    assert after == before  # schedule and history untouched


async def test_topic_change_keeps_old_material_in_plan(
    migrated_db: None, db_session: AsyncSession
) -> None:
    user_id, _, expressions = await create_user_with_progress(db_session)
    before = await snapshot_progress(db_session, user_id)

    service = ProfileService(ProfileRepository(db_session), make_analytics(db_session))
    updated = await service.update_profile(
        user_id, ProfileUpdate(topics=["travel", "science"])
    )

    assert sorted(updated.topics) == ["science", "travel"]
    # Progress rows (old material) still exist and are unchanged.
    assert await snapshot_progress(db_session, user_id) == before
    served_ids = {row["expression_id"] for row in before}
    assert served_ids == {expression.id for expression in expressions}


async def test_language_change_preserves_progress(
    migrated_db: None, db_session: AsyncSession
) -> None:
    """Switching to ES keeps the EN cards scheduled (задел, not a reset)."""
    user_id, _, _ = await create_user_with_progress(db_session)
    before = await snapshot_progress(db_session, user_id)

    service = ProfileService(ProfileRepository(db_session), make_analytics(db_session))
    updated = await service.update_profile(user_id, ProfileUpdate(target_language="es"))

    assert updated.target_language == "es"
    assert await snapshot_progress(db_session, user_id) == before


async def test_full_onboarding_rerun_keeps_progress(
    migrated_db: None, db_session: AsyncSession
) -> None:
    """Re-running onboarding replaces the profile but never the history."""
    user_id, _, _ = await create_user_with_progress(db_session)
    before = await snapshot_progress(db_session, user_id)

    service = ProfileService(ProfileRepository(db_session), make_analytics(db_session))
    await service.save_profile(
        user_id,
        ProfileCreate.model_validate(profile_payload(level="B1", topics=["movies"])),
    )

    profile = await ProfileRepository(db_session).get_by_user_id(user_id)
    assert profile is not None
    assert profile.level == "B1"
    assert profile.topics == ["movies"]
    assert await snapshot_progress(db_session, user_id) == before
