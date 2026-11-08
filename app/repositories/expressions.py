"""Repository for the ``expressions`` table."""

from sqlalchemy import ColumnElement, func, select
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
        owner_user_id: int | None = None,
    ) -> list[Expression]:
        """Active expressions matching the filters, deterministically ordered.

        ``owner_user_id`` scopes visibility: shared catalogue entries
        (``owner_user_id IS NULL``) plus the caller's own personal cards
        — nobody else's. ``topics=None`` (or an empty list) means "any
        topic"; otherwise only the listed topics are returned. Ordering
        by id keeps lessons reproducible for tests and stable for users.
        """
        query = (
            select(Expression)
            .where(
                Expression.language == language,
                Expression.level == level,
                Expression.is_active.is_(True),
                self._visible_to(owner_user_id),
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
        owner_user_id: int | None = None,
    ) -> int:
        """Number of active expressions matching the filters."""
        query = (
            select(func.count())
            .select_from(Expression)
            .where(
                Expression.language == language,
                Expression.is_active.is_(True),
                self._visible_to(owner_user_id),
            )
        )
        if topics:
            query = query.where(Expression.topic.in_(topics))
        if level is not None:
            query = query.where(Expression.level == level)
        async with wrap_storage_errors():
            result = await self._session.execute(query)
            return int(result.scalar_one())

    async def add(self, expression: Expression) -> Expression:
        """Insert a new catalogue card (flushed, not committed here)."""
        async with wrap_storage_errors():
            self._session.add(expression)
            await self._session.flush()
            await self._session.refresh(expression)
            return expression

    async def find_existing_texts(self, language: str, texts: list[str]) -> set[str]:
        """Lowercased texts that already exist in the catalogue/language.

        Case-insensitive on purpose: "break a leg" and "Break a Leg!"
        are the same expression for duplication purposes.
        """
        if not texts:
            return set()
        lowered = [text.lower() for text in texts]
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(func.lower(Expression.text)).where(
                    Expression.language == language,
                    func.lower(Expression.text).in_(lowered),
                )
            )
            return {row[0] for row in result.all()}

    async def get_by_language_and_text(
        self, language: str, text: str
    ) -> Expression | None:
        """Shared-catalogue lookup used by the acceptance flow.

        Only owner-less cards are reusable: matching someone's *personal*
        card must not attach another user's progress to it — the caller
        creates an own card instead (allowed by the partial unique
        indexes).
        """
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(Expression).where(
                    Expression.language == language,
                    func.lower(Expression.text) == text.lower(),
                    Expression.owner_user_id.is_(None),
                )
            )
            return result.scalars().first()

    @staticmethod
    def _visible_to(owner_user_id: int | None) -> ColumnElement[bool]:
        """Shared catalogue plus the caller's own personal cards."""
        return Expression.owner_user_id.is_(None) | (
            Expression.owner_user_id == owner_user_id
        )
