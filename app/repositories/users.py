"""Repositories for users and their external identities."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserIdentity
from app.repositories.errors import StorageError, wrap_storage_errors

TELEGRAM_PROVIDER = "telegram"


class UserRepository:
    """Data access for internal users and their sign-in identities.

    Never commits: transaction boundaries belong to ``session_scope``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> User | None:
        """Return the internal user by id."""
        async with wrap_storage_errors():
            result = await self._session.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()

    async def get_by_identity(
        self, provider: str, provider_user_id: str
    ) -> tuple[User, UserIdentity] | None:
        """Return the user bound to an external identity, if any."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(User, UserIdentity)
                .join(UserIdentity, UserIdentity.user_id == User.id)
                .where(
                    UserIdentity.provider == provider,
                    UserIdentity.provider_user_id == provider_user_id,
                )
            )
            return result.first()

    async def get_or_create_by_identity(
        self, provider: str, provider_user_id: str
    ) -> tuple[User, bool]:
        """Return ``(user, created)`` for an external identity.

        The unique (provider, provider_user_id) pair guarantees one
        internal account per external identity; a concurrent duplicate
        insert surfaces as a conflict :class:`StorageError`.
        """
        existing = await self.get_by_identity(provider, provider_user_id)
        if existing is not None:
            return existing[0], False
        user = User()
        self._session.add(user)
        try:
            await self._session.flush()
            self._session.add(
                UserIdentity(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                )
            )
            await self._session.flush()
        except IntegrityError as error:
            raise StorageError(
                f"identity {provider}/{provider_user_id} already exists",
                conflict=True,
            ) from error
        except SQLAlchemyError as error:
            raise StorageError("failed to create user") from error
        await self._session.refresh(user)
        return user, True

    async def link_identity(
        self, user_id: int, provider: str, provider_user_id: str
    ) -> UserIdentity:
        """Attach another sign-in identity to an existing user.

        Raises :class:`StorageError` with ``conflict=True`` when the
        identity is already bound (to anyone, including this user) —
        callers must never silently rebind another person's identity.
        """
        existing = await self.get_by_identity(provider, provider_user_id)
        if existing is not None:
            raise StorageError(
                f"identity {provider}/{provider_user_id} already linked",
                conflict=True,
            )
        identity = UserIdentity(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
        )
        self._session.add(identity)
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise StorageError(
                f"identity {provider}/{provider_user_id} already linked",
                conflict=True,
            ) from error
        except SQLAlchemyError as error:
            raise StorageError("failed to link identity") from error
        await self._session.refresh(identity)
        return identity
