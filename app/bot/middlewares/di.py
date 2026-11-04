"""Dependency-injection middleware.

Opens one database session per Telegram update and exposes the session
and ready-made services to handlers through aiogram's dependency
injection data, so handlers stay free of storage code. The session is
transactional: it commits when the handler succeeds and rolls back when
it raises (see ``session_scope``); analytics events share that
transaction.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.session import SessionFactory, session_scope
from app.repositories import (
    AnalyticsRepository,
    ApiTokenRepository,
    ExpressionProgressRepository,
    ExpressionRepository,
    ProfileRepository,
    UserRepository,
)
from app.services import (
    AnalyticsService,
    ApiTokenService,
    LessonService,
    ProfileService,
    ReviewService,
    StatsService,
    UserService,
)


class DbSessionMiddleware(BaseMiddleware):
    """Provide per-update session and application services."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope(self._session_factory) as session:
            analytics = AnalyticsService(AnalyticsRepository(session))
            data["session"] = session
            data["user_service"] = UserService(UserRepository(session), analytics)
            data["profile_service"] = ProfileService(
                ProfileRepository(session), analytics
            )
            progress_repository = ExpressionProgressRepository(session)
            expression_repository = ExpressionRepository(session)
            data["lesson_service"] = LessonService(
                expression_repository, progress_repository, analytics
            )
            data["review_service"] = ReviewService(
                progress_repository, expression_repository, analytics
            )
            data["stats_service"] = StatsService(session, AnalyticsRepository(session))
            data["token_service"] = ApiTokenService(ApiTokenRepository(session))
            return await handler(event, data)
