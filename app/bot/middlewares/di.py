"""Dependency-injection middleware.

Opens one database session per Telegram update and exposes the session and
a ready-made :class:`~app.services.users.UserService` to handlers through
aiogram's dependency-injection data, so handlers stay free of storage code.

The session is transactional: it commits when the handler succeeds and
rolls back when it raises (see ``session_scope``).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.session import SessionFactory, session_scope
from app.repositories import UserRepository
from app.services import UserService


class DbSessionMiddleware(BaseMiddleware):
    """Provide ``session`` and ``user_service`` to every update handler."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope(self._session_factory) as session:
            data["session"] = session
            data["user_service"] = UserService(UserRepository(session))
            return await handler(event, data)
