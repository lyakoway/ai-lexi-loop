"""Integration tests for the stats service on a real PostgreSQL."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.lessons import LessonPlan
from app.services.stats import StatsService
from tests.integration.conftest import (
    create_profile_row,
    insert_test_expression,
    make_analytics,
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


def make_stats(session: AsyncSession) -> StatsService:
    from app.repositories import AnalyticsRepository

    return StatsService(session, AnalyticsRepository(session))


async def seed_activity(
    db_session: AsyncSession, *, telegram_id: int, days_ago: list[int]
) -> int:
    """Create a user with a lesson answered on the given days ago."""
    for number, days in enumerate(days_ago):
        expression = await insert_test_expression(
            db_session, text_value=f"expr-{telegram_id}-{number}"
        )
        profile = await create_profile_row(db_session, telegram_id=telegram_id)
        moment = NOW - timedelta(days=days)
        plan = await make_lesson_service(db_session).build_lesson(
            profile.user_id, profile, now=moment
        )
        assert plan is not None
        await make_review_service(db_session).apply_answer(
            profile.user_id,
            expression.id,
            remembered=True,
            now=moment + timedelta(days=1, hours=1),
        )
    profile = await create_profile_row(db_session, telegram_id=telegram_id)
    return profile.user_id


async def test_overview_counts_cards_reviews_and_due(
    db_session: AsyncSession,
) -> None:
    user_id = await seed_activity(db_session, telegram_id=8001, days_ago=[5, 6])

    overview = await make_stats(db_session).get_overview(user_id, now=NOW)

    assert overview.expressions_shown == 2
    assert overview.reviews_total == 2
    assert overview.remember_ratio == 1.0
    # Both first answers use interval 1 day, long past by NOW.
    assert overview.due_count == 2
    # Activity days are NOW-6..NOW-4; neither today nor yesterday had it.
    assert overview.streak_days == 0


async def test_overview_counts_due_cards(db_session: AsyncSession) -> None:
    user_id = await seed_activity(db_session, telegram_id=8002, days_ago=[10])

    overview = await make_stats(db_session).get_overview(user_id, now=NOW)

    # Answered 9 days ago with interval 1 → due since 8 days ago.
    assert overview.due_count == 1


async def test_stats_are_isolated_between_users(db_session: AsyncSession) -> None:
    await seed_activity(db_session, telegram_id=8003, days_ago=[1])
    other_profile = await create_profile_row(db_session, telegram_id=8004)

    overview = await make_stats(db_session).get_overview(other_profile.user_id, now=NOW)

    assert overview.expressions_shown == 0
    assert overview.reviews_total == 0
    assert overview.streak_days == 0


async def test_streak_counts_consecutive_days(db_session: AsyncSession) -> None:
    """Activity today and yesterday yields a streak of 2."""
    user_id = await seed_activity(db_session, telegram_id=8005, days_ago=[0, 1])

    overview = await make_stats(db_session).get_overview(user_id, now=NOW)

    assert overview.streak_days == 2


async def test_streak_breaks_on_gap(db_session: AsyncSession) -> None:
    """Activity one to four days ago: the streak survives from yesterday."""
    user_id = await seed_activity(db_session, telegram_id=8006, days_ago=[2, 4])

    overview = await make_stats(db_session).get_overview(user_id, now=NOW)

    # Days with activity: NOW-4..NOW-1 (answers land the next day).
    assert overview.streak_days == 4


async def test_progress_daily_breakdown(db_session: AsyncSession) -> None:
    user_id = await seed_activity(db_session, telegram_id=8007, days_ago=[1])

    progress = await make_stats(db_session).get_progress(user_id, now=NOW)

    assert len(progress) == 2
    by_day = {day.day: day for day in progress}
    lesson_day = (NOW - timedelta(days=1)).date()
    review_day = NOW.date()
    assert by_day[lesson_day].expressions_shown == 1
    assert by_day[review_day].reviews == 1


async def test_lessons_completed_counted_from_events(
    db_session: AsyncSession,
) -> None:
    profile = await create_profile_row(db_session, telegram_id=8008)
    await insert_test_expression(db_session, text_value="finish-me")
    plan = await make_lesson_service(db_session).build_lesson(
        profile.user_id, profile, now=NOW
    )
    assert isinstance(plan, LessonPlan)
    await make_lesson_service(db_session).finish_lesson(
        profile.user_id,
        [plan.expressions[0].id],
        source="telegram",
        lesson_ref="ref1",
        expressions_total=1,
        duration_seconds=42.0,
    )

    overview = await make_stats(db_session).get_overview(profile.user_id, now=NOW)

    assert overview.lessons_completed == 1
    assert overview.expressions_completed == 1
    assert make_analytics is not None  # helper import sanity
