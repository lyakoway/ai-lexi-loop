"""User registration business logic."""

import logging

from app.db.models import User
from app.repositories import UserRepository
from app.repositories.users import TELEGRAM_PROVIDER
from app.services.analytics import (
    USER_REGISTERED,
    AnalyticsService,
    EventSource,
)

logger = logging.getLogger(__name__)


class UserService:
    """Registers and looks up internal users via external identities."""

    def __init__(self, repository: UserRepository, analytics: AnalyticsService) -> None:
        self._repository = repository
        self._analytics = analytics

    async def ensure_registered(
        self, telegram_user_id: int, *, source: EventSource = "telegram"
    ) -> tuple[User, bool]:
        """Return ``(user, created)`` for a Telegram identity.

        ``created`` is ``False`` for an existing user and for the rare
        concurrent-duplicate conflict, since in both cases the account
        already exists.
        """
        user, created = await self._repository.get_or_create_by_identity(
            TELEGRAM_PROVIDER, str(telegram_user_id)
        )
        if created:
            logger.info("registered new user telegram_user_id=%s", telegram_user_id)
            await self._analytics.track(
                user.id, USER_REGISTERED, source, {"provider": "telegram"}
            )
        return user, created

    async def get_user(self, user_id: int) -> User | None:
        """Return the internal user by id."""
        return await self._repository.get_by_id(user_id)

    async def link_telegram(self, user_id: int, telegram_user_id: int) -> None:
        """Attach a Telegram identity to an existing internal user.

        Raises :class:`StorageError` (conflict) when the Telegram ID is
        already linked to any account — rebinds are never implicit.
        """
        await self._repository.link_identity(
            user_id, TELEGRAM_PROVIDER, str(telegram_user_id)
        )
