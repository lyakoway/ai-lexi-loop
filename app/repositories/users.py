"""Repository for the ``users`` table."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.repositories.errors import wrap_storage_errors


class UserRepository:
    """Data access for :class:`~app.db.models.User`.

    The repository takes an ``AsyncSession`` and never commits: transaction
    boundaries belong to the caller (``session_scope``), so several
    repository calls can share one transaction when needed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        """Return the user with the given Telegram ID, or ``None``."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(User).where(User.telegram_user_id == telegram_user_id)
            )
            return result.scalar_one_or_none()

    async def create(self, telegram_user_id: int) -> User:
        """Add a new user to the session (flushed, not committed here)."""
        async with wrap_storage_errors():
            user = User(telegram_user_id=telegram_user_id)
            self._session.add(user)
            await self._session.flush()
            await self._session.refresh(user)
            return user

    async def get_or_create(self, telegram_user_id: int) -> tuple[User, bool]:
        """Return ``(user, created)`` for the given Telegram ID.

        Raises :class:`StorageError` with ``conflict=True`` when a concurrent
        transaction inserted the same Telegram ID first. The caller is
        expected to treat that user as already registered.
        """
        async with wrap_storage_errors():
            user = await self.get_by_telegram_id(telegram_user_id)
            if user is not None:
                return user, False
            user = User(telegram_user_id=telegram_user_id)
            self._session.add(user)
            await self._session.flush()
            await self._session.refresh(user)
            return user, True
