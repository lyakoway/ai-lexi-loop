"""Repository for the ``expressions`` table."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expression
from app.repositories.errors import wrap_storage_errors


class ExpressionRepository:
    """Data access for dictionary expressions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, expression_id: int) -> Expression | None:
        """Return the expression regardless of activity status."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(Expression).where(Expression.id == expression_id)
            )
            return result.scalar_one_or_none()

    async def get_many_by_ids(self, expression_ids: list[int]) -> list[Expression]:
        """Return expressions for the given ids, ordered by id."""
        if not expression_ids:
            return []
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(Expression)
                .where(Expression.id.in_(expression_ids))
                .order_by(Expression.id)
            )
            return list(result.scalars().all())

    async def get_active(
        self,
        *,
        language: str,
        level: str,
        topics: list[str] | None,
        limit: int,
        exclude_ids: set[int] | None = None,
    ) -> list[Expression]:
        """Active expressions matching the filters, deterministically ordered.

        ``topics=None`` (or an empty list) means "any topic"; otherwise
        only the listed topics are returned. Ordering by id keeps lessons
        reproducible for tests and stable for users.
        """
        query = (
            select(Expression)
            .where(
                Expression.language == language,
                Expression.level == level,
                Expression.is_active.is_(True),
            )
            .order_by(Expression.id)
            .limit(limit)
        )
        if topics:
            query = query.where(Expression.topic.in_(topics))
        if exclude_ids:
            query = query.where(~Expression.id.in_(exclude_ids))
        async with wrap_storage_errors():
            result = await self._session.execute(query)
            return list(result.scalars().all())

    async def count_active(
        self,
        *,
        language: str,
        topics: list[str] | None,
        level: str | None = None,
    ) -> int:
        """Number of active expressions matching the filters."""
        query = (
            select(func.count())
            .select_from(Expression)
            .where(
                Expression.language == language,
                Expression.is_active.is_(True),
            )
        )
        if topics:
            query = query.where(Expression.topic.in_(topics))
        if level is not None:
            query = query.where(Expression.level == level)
        async with wrap_storage_errors():
            result = await self._session.execute(query)
            return int(result.scalar_one())
