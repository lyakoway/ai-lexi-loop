"""Unit tests for the analytics service and stats/token handlers."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from app.bot import texts
from app.bot.handlers import stats_token as stats_token_handlers
from app.bot.stats_view import format_overview
from app.repositories import AnalyticsRepository, StorageError
from app.services.analytics import AnalyticsService
from app.services.stats import StatsOverview
from app.services.tokens import ApiTokenService, TokenLimitError

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def make_service() -> tuple[AnalyticsService, AsyncMock]:
    repository = AsyncMock(spec=AnalyticsRepository)
    return AnalyticsService(repository), repository


async def test_track_delegates_to_repository() -> None:
    service, repository = make_service()

    await service.track(11, "lesson_started", "web", {"count": 3}, occurred_at=NOW)

    repository.add.assert_awaited_once_with(
        11, "lesson_started", "web", {"count": 3}, occurred_at=NOW
    )


async def test_track_rejects_sensitive_properties() -> None:
    service, repository = make_service()

    with pytest.raises(ValueError, match="sensitive"):
        await service.track(11, "event", "web", {"token": "secret"})

    repository.add.assert_not_awaited()


async def test_track_accepts_scalar_properties_only() -> None:
    service, repository = make_service()

    await service.track(
        11,
        "event",
        "telegram",
        {"answer": "remember", "interval_after": 6, "ok": True, "ref": None},
    )

    repository.add.assert_awaited_once()


def test_format_overview_renders_all_metrics() -> None:
    rendered = format_overview(
        StatsOverview(
            expressions_shown=10,
            expressions_completed=8,
            lessons_completed=3,
            reviews_total=5,
            remember_ratio=0.8,
            due_count=2,
            streak_days=4,
        )
    )
    assert "10" in rendered
    assert "80%" in rendered
    assert "4" in rendered


def test_format_overview_without_reviews_shows_dash() -> None:
    rendered = format_overview(
        StatsOverview(
            expressions_shown=0,
            expressions_completed=0,
            lessons_completed=0,
            reviews_total=0,
            remember_ratio=None,
            due_count=0,
            streak_days=0,
        )
    )
    assert "—" in rendered


def make_message() -> Any:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 12345
    return message


def make_user_service() -> Any:
    from unittest.mock import AsyncMock

    from app.services import UserService

    service = AsyncMock(spec=UserService)
    service.ensure_registered.return_value = (MagicMock(id=11), True)
    return service


async def test_cmd_stats_shows_overview() -> None:
    from app.services import StatsService

    message = make_message()
    user_service = make_user_service()
    stats_service = AsyncMock(spec=StatsService)
    stats_service.get_overview.return_value = StatsOverview(
        expressions_shown=1,
        expressions_completed=1,
        lessons_completed=1,
        reviews_total=1,
        remember_ratio=1.0,
        due_count=0,
        streak_days=1,
    )

    await stats_token_handlers.cmd_stats(message, user_service, stats_service)

    stats_service.get_overview.assert_awaited_once_with(11)
    message.answer.assert_awaited_once()


async def test_cmd_stats_reports_storage_failure() -> None:
    from app.services import StatsService

    message = make_message()
    stats_service = AsyncMock(spec=StatsService)
    stats_service.get_overview.side_effect = StorageError("db down")

    await stats_token_handlers.cmd_stats(message, make_user_service(), stats_service)

    sent = message.answer.await_args.args[0]
    assert "недоступен" in sent


async def test_cmd_token_shows_token_once() -> None:
    from app.services.tokens import IssuedToken

    message = make_message()
    token_service = AsyncMock(spec=ApiTokenService)
    token_service.issue.return_value = IssuedToken(token="alex_secret", id=7)

    await stats_token_handlers.cmd_token(message, make_user_service(), token_service)

    token_service.issue.assert_awaited_once_with(11)
    sent = message.answer.await_args.args[0]
    assert "alex_secret" in sent
    assert texts.TOKEN_INSTRUCTIONS in sent


async def test_cmd_token_reports_limit() -> None:
    message = make_message()
    token_service = AsyncMock(spec=ApiTokenService)
    token_service.issue.side_effect = TokenLimitError("too many")

    await stats_token_handlers.cmd_token(message, make_user_service(), token_service)

    sent = message.answer.await_args.args[0]
    assert "максимум" in sent
