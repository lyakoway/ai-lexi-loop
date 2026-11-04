"""Repository for personal API tokens."""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiToken
from app.repositories.errors import wrap_storage_errors


class ApiTokenRepository:
    """Data access for bearer tokens (hashes only)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: int, token_hash: str) -> ApiToken:
        """Store the hash of a freshly issued token."""
        async with wrap_storage_errors():
            token = ApiToken(user_id=user_id, token_hash=token_hash)
            self._session.add(token)
            await self._session.flush()
            await self._session.refresh(token)
            return token

    async def find_active_by_hash(
        self, token_hash: str, *, now: datetime
    ) -> ApiToken | None:
        """Return the non-revoked token row with this hash, if any."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(ApiToken).where(
                    ApiToken.token_hash == token_hash,
                    ApiToken.revoked_at.is_(None),
                )
            )
            return result.scalar_one_or_none()

    async def touch(self, token_id: int, *, now: datetime) -> None:
        """Record the last use of a token for observability."""
        async with wrap_storage_errors():
            await self._session.execute(
                update(ApiToken).where(ApiToken.id == token_id).values(last_used_at=now)
            )

    async def count_for_user(self, user_id: int) -> int:
        """Number of non-revoked tokens of the user."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(func.count())
                .select_from(ApiToken)
                .where(
                    ApiToken.user_id == user_id,
                    ApiToken.revoked_at.is_(None),
                )
            )
            return int(result.scalar_one())
