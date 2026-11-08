"""Repository for AI-generated expression drafts."""

from datetime import datetime
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserGeneratedExpression
from app.repositories.errors import wrap_storage_errors


class UserGeneratedExpressionRepository:
    """Data access for personal drafts; every query is user-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_drafts(
        self, rows: list[UserGeneratedExpression]
    ) -> list[UserGeneratedExpression]:
        """Insert the drafts in the current transaction."""
        if not rows:
            return []
        async with wrap_storage_errors():
            self._session.add_all(rows)
            await self._session.flush()
            for row in rows:
                await self._session.refresh(row)
            return list(rows)

    async def get_by_ids(
        self, user_id: int, draft_ids: list[int]
    ) -> list[UserGeneratedExpression]:
        """The user's drafts with the given ids (ownership enforced)."""
        if not draft_ids:
            return []
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(UserGeneratedExpression)
                .where(
                    UserGeneratedExpression.user_id == user_id,
                    UserGeneratedExpression.id.in_(draft_ids),
                )
                .order_by(UserGeneratedExpression.id)
            )
            return list(result.scalars().all())

    async def count_user_draft_texts(
        self, user_id: int, lowered_texts: list[str]
    ) -> set[str]:
        """Lowercased draft texts the user already has (any status)."""
        if not lowered_texts:
            return set()
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(func.lower(UserGeneratedExpression.text)).where(
                    UserGeneratedExpression.user_id == user_id,
                    func.lower(UserGeneratedExpression.text).in_(lowered_texts),
                )
            )
            return {row[0] for row in result.all()}

    async def mark_accepted(
        self,
        draft_id: int,
        *,
        accepted_expression_id: int,
        now: datetime,
    ) -> None:
        """Stamp the draft as accepted, pointing at its catalogue card."""
        async with wrap_storage_errors():
            await self._session.execute(
                update(UserGeneratedExpression)
                .where(UserGeneratedExpression.id == draft_id)
                .values(
                    status="accepted",
                    accepted_expression_id=accepted_expression_id,
                    updated_at=now,
                )
            )

    async def mark_rejected(self, draft_id: int, *, now: datetime) -> None:
        """Stamp the draft as rejected; it will never enter lessons."""
        async with wrap_storage_errors():
            await self._session.execute(
                update(UserGeneratedExpression)
                .where(UserGeneratedExpression.id == draft_id)
                .values(status="rejected", updated_at=now)
            )

    async def count_by_status(
        self,
        user_id: int,
        status: Literal["draft", "accepted", "rejected"],
    ) -> int:
        """How many drafts of the user are in the given status."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(func.count())
                .select_from(UserGeneratedExpression)
                .where(
                    UserGeneratedExpression.user_id == user_id,
                    UserGeneratedExpression.status == status,
                )
            )
            return int(result.scalar_one())
