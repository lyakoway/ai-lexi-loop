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

from app.core.config import get_settings
from app.db.session import SessionFactory, session_scope
from app.integrations.llm.client import StructuredLlmClient
from app.integrations.tts.provider import TtsProvider
from app.repositories import (
    AnalyticsRepository,
    ApiTokenRepository,
    ExpressionProgressRepository,
    ExpressionRepository,
    ProfileRepository,
    UserGeneratedExpressionRepository,
    UserRepository,
)
from app.services import (
    AnalyticsService,
    ApiTokenService,
    ChatService,
    LessonService,
    ProfileService,
    ReviewService,
    StatsService,
    TutorService,
    UserService,
)
from app.services.tts import TtsService
from app.services.tutor import GenerationService


class DbSessionMiddleware(BaseMiddleware):
    """Provide per-update session, application services and the LLM client."""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm_client: StructuredLlmClient | None = None,
        tts_provider: TtsProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._llm_client = llm_client
        self._tts_provider = tts_provider

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope(self._session_factory) as session:
            analytics = AnalyticsService(AnalyticsRepository(session))
            # Deterministic idempotency key source for chat messages.
            update_id = getattr(event, "update_id", None)
            data["tg_update_id"] = update_id
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
            data["generation_service"] = GenerationService(
                data["profile_service"],
                expression_repository,
                UserGeneratedExpressionRepository(session),
                self._llm_client,
                analytics,
            )
            data["chat_service"] = ChatService(
                self._session_factory, self._llm_client, get_settings()
            )
            data["tts_service"] = TtsService(
                self._session_factory,
                self._tts_provider,
                get_settings().tts_cache_dir,
                default_voice=get_settings().tts_voice,
            )
            data["tts_available"] = self._tts_provider is not None
            data["token_service"] = ApiTokenService(ApiTokenRepository(session))
            data["tutor_service"] = TutorService(
                data["profile_service"],
                expression_repository,
                progress_repository,
                self._llm_client,
                analytics,
            )
            data["llm_client"] = self._llm_client
            return await handler(event, data)
