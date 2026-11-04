"""Integration tests for analytics events on a real PostgreSQL."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalyticsEvent
from app.db.session import SessionFactory, session_scope
from app.services.lessons import LessonPlan
from tests.integration.conftest import (
    create_profile_row,
    insert_test_expression,
    make_lesson_service,
    make_review_service,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


async def all_events(
    db_session: AsyncSession, user_id: int
) -> list[tuple[str, str, dict[str, object]]]:
    rows = (
        (
            await db_session.execute(
                select(AnalyticsEvent).where(AnalyticsEvent.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return [(e.event_name, e.source, e.properties) for e in rows]


async def test_registration_and_profile_events(db_session: AsyncSession) -> None:
    """Fresh users get user_registered; profile save emits events."""
    from app.repositories import UserRepository
    from app.services import UserService
    from tests.integration.conftest import make_analytics

    analytics = make_analytics(db_session)
    users = UserRepository(db_session)
    user, created = await UserService(users, analytics).ensure_registered(7001)
    assert created is True
    await create_profile_row(db_session, telegram_id=7001)

    events = await all_events(db_session, user.id)
    names = [name for name, _, _ in events]
    assert "user_registered" in names
    assert "profile_completed" in names


async def test_lesson_events_written_with_source(db_session: AsyncSession) -> None:
    """build_lesson emits lesson_started and expression_shown per card."""
    await insert_test_expression(db_session, text_value="one")
    await insert_test_expression(db_session, text_value="two")
    profile = await create_profile_row(db_session)

    plan = await make_lesson_service(db_session).build_lesson(
        profile.user_id, profile, now=NOW, source="web"
    )

    assert plan is not None
    events = await all_events(db_session, profile.user_id)
    names = [name for name, _, _ in events]
    assert names.count("lesson_started") == 1
    assert names.count("expression_shown") == 2
    lesson_event = next(e for e in events if e[0] == "lesson_started")
    assert lesson_event[1] == "web"
    assert lesson_event[2]["expression_count"] == 2


async def test_review_answer_event_carries_intervals(
    db_session: AsyncSession,
) -> None:
    await insert_test_expression(db_session, text_value="one")
    profile = await create_profile_row(db_session)
    plan = await make_lesson_service(db_session).build_lesson(
        profile.user_id, profile, now=NOW
    )
    assert isinstance(plan, LessonPlan)
    expression_id = plan.expressions[0].id

    update = await make_review_service(db_session).apply_answer(
        profile.user_id,
        expression_id,
        remembered=True,
        now=NOW + timedelta(days=1),
    )

    assert update is not None
    events = await all_events(db_session, profile.user_id)
    answered = next(e for e in events if e[0] == "review_answered")
    assert answered[2]["expression_id"] == expression_id
    assert answered[2]["answer"] == "remember"
    assert answered[2]["interval_before"] == 1
    assert answered[2]["interval_after"] == update.interval_days


async def test_events_rollback_with_business_change(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """A rolled-back operation leaves no events behind."""
    async with session_scope(session_factory) as session:
        await insert_test_expression(session, text_value="rollback-unique")
        profile = await create_profile_row(session, telegram_id=7100)
        user_id = profile.user_id
        await make_lesson_service(session).build_lesson(user_id, profile, now=NOW)
        # Roll the whole transaction back — as a failure would.
        await session.rollback()

    async with session_scope(session_factory) as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(AnalyticsEvent.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        assert events == []


async def test_events_table_shape(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    from sqlalchemy import text as sql_text

    async with session_factory() as session:
        columns = {
            row[0]
            for row in (
                await session.execute(
                    sql_text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'analytics_events'"
                    )
                )
            ).all()
        }
    assert {
        "id",
        "user_id",
        "event_name",
        "source",
        "properties",
        "occurred_at",
        "created_at",
    }.issubset(columns)
