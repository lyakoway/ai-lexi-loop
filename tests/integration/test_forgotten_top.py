"""Integration tests for the weekly "top forgotten" report on real PostgreSQL.

Ordering: most lapses first, then the most overdue card; cards never
answered wrong are not reported and the size is capped at 10. The
report is throttled to once per ``forgotten_report_interval_days`` via
``profiles.last_forgotten_report_at`` — and an empty report does not
arm the throttle.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Expression, ExpressionProgress, Profile
from app.db.session import SessionFactory, session_scope
from app.services.quiz import (
    ForgottenReport,
    ForgottenThrottled,
    QuizService,
)
from tests.conftest import make_settings
from tests.integration.conftest import create_profile_row, reset_database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
WEEK = timedelta(days=7)


async def prepare_user(session_factory: SessionFactory, telegram_id: int) -> int:
    async with session_scope(session_factory) as session:
        await reset_database(session)
        profile = await create_profile_row(session, telegram_id=telegram_id)
        return profile.user_id


async def seed_with_lapses(
    session_factory: SessionFactory,
    user_id: int,
    text: str,
    *,
    lapse_count: int,
    overdue_days: int = 0,
) -> int:
    """Serve ``text`` to the user with a given lapse history."""
    async with session_scope(session_factory) as session:
        expression = Expression(
            language="ts",
            text=text,
            translation="перевод",
            ipa="aɪ piː eɪ",
            phonetic_hint_ru="пОдсказка",
            example="example",
            example_translation="пример",
            level="A1",
            topic="everyday",
        )
        session.add(expression)
        await session.flush()
        session.add(
            ExpressionProgress(
                user_id=user_id,
                expression_id=expression.id,
                lapse_count=lapse_count,
                interval_days=1 if lapse_count else 6,
                next_review_at=NOW - timedelta(days=overdue_days),
            )
        )
        await session.flush()
        return expression.id


async def load_report_time(session_factory: SessionFactory, user_id: int) -> object:
    async with session_scope(session_factory) as session:
        result = await session.execute(
            select(Profile).where(Profile.user_id == user_id)
        )
        profile = result.scalar_one()
        return profile.last_forgotten_report_at


def make_service(session_factory: SessionFactory) -> QuizService:
    settings = make_settings(
        database_url="postgresql://u:p@h/db", forgotten_report_interval_days=7
    )
    return QuizService(session_factory, None, settings)


async def test_report_orders_by_lapses_then_overdue(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9900)
    most = await seed_with_lapses(
        session_factory, user_id, "Most lapses", lapse_count=3, overdue_days=1
    )
    overdue = await seed_with_lapses(
        session_factory, user_id, "Single lapse overdue", lapse_count=1, overdue_days=5
    )
    fresh = await seed_with_lapses(
        session_factory, user_id, "Single lapse fresh", lapse_count=1, overdue_days=0
    )
    await seed_with_lapses(
        session_factory, user_id, "Never forgotten", lapse_count=0, overdue_days=9
    )

    report = await make_service(session_factory).forgotten_report(user_id, now=NOW)

    assert isinstance(report, ForgottenReport)
    texts = [item.text for item in report.items]
    assert texts == ["Most lapses", "Single lapse overdue", "Single lapse fresh"]
    assert {most, overdue, fresh}  # ids were created; ordering is by lapses
    assert report.items[0].lapse_count == 3
    # The never-forgotten card is not in the report.
    assert "Never forgotten" not in texts


async def test_report_capped_at_ten_items(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9901)
    for number in range(12):
        await seed_with_lapses(
            session_factory, user_id, f"phrase {number:02d}", lapse_count=number + 1
        )

    report = await make_service(session_factory).forgotten_report(user_id, now=NOW)

    assert isinstance(report, ForgottenReport)
    assert len(report.items) == 10


async def test_report_throttled_to_once_per_week(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9902)
    await seed_with_lapses(session_factory, user_id, "Forgotten once", lapse_count=1)
    service = make_service(session_factory)

    first = await service.forgotten_report(user_id, now=NOW)
    assert isinstance(first, ForgottenReport) and len(first.items) == 1
    assert await load_report_time(session_factory, user_id) == NOW

    throttled = await service.forgotten_report(user_id, now=NOW + timedelta(hours=1))
    assert isinstance(throttled, ForgottenThrottled)
    assert throttled.next_allowed_at == NOW + WEEK

    # After the interval the report is allowed again.
    second = await service.forgotten_report(
        user_id, now=NOW + WEEK + timedelta(hours=2)
    )
    assert isinstance(second, ForgottenReport)


async def test_empty_report_does_not_arm_the_throttle(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9903)
    await seed_with_lapses(session_factory, user_id, "Clean card", lapse_count=0)
    service = make_service(session_factory)

    empty = await service.forgotten_report(user_id, now=NOW)
    assert isinstance(empty, ForgottenReport)
    assert empty.items == []
    assert await load_report_time(session_factory, user_id) is None

    # Still free to ask again immediately.
    again = await service.forgotten_report(user_id, now=NOW + timedelta(minutes=1))
    assert isinstance(again, ForgottenReport)
