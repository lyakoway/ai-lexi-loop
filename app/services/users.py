"""User registration business logic."""

import logging

from app.db.models import User
from app.repositories import UserRepository

logger = logging.getLogger(__name__)


class UserService:
    """Registers and looks up trainer users."""

    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    async def ensure_registered(self, telegram_user_id: int) -> tuple[User, bool]:
        """Return ``(user, created)`` for a Telegram user.

        ``created`` is ``False`` both for an existing user and for the rare
        concurrent-duplicate conflict, since in both cases the account
        already exists.
        """
        user, created = await self._repository.get_or_create(telegram_user_id)
        if created:
            logger.info("registered new user telegram_user_id=%s", telegram_user_id)
        return user, created
