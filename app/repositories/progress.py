"""Repository for the ``expression_progress`` table."""

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExpressionProgress
from app.domain.srs import FIRST_INTERVAL_DAYS, SrsUpdate
from app.repositories.errors import wrap_storage_errors


class ExpressionProgressRepository:
    """Data access for per-user expression progress and SRS state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_served_expression_ids(self, user_id: int) -> set[int]:
        """Ids of expressions ever served to the user."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(ExpressionProgress.expression_id).where(
                    ExpressionProgress.user_id == user_id
                )
            )
            return {row[0] for row in result.all()}

    async def get_by_pair(
        self, user_id: int, expression_id: int
    ) -> ExpressionProgress | None:
        """The progress row of one (user, expression) pair, if any."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(ExpressionProgress).where(
                    ExpressionProgress.user_id == user_id,
                    ExpressionProgress.expression_id == expression_id,
                )
            )
            return result.scalar_one_or_none()

    async def bulk_create(
        self,
        user_id: int,
        expression_ids: list[int],
        *,
        now: datetime,
    ) -> list[ExpressionProgress]:
        """Record served expressions and schedule their first review.

        The first ``next_review_at`` is set to ``now + 1 day``, so a card
        returns via /review even when its lesson was abandoned.
        Raises :class:`~app.repositories.errors.StorageError` with
        ``conflict=True`` when some pair already exists.
        """
        async with wrap_storage_errors():
            rows = [
                ExpressionProgress(
                    user_id=user_id,
                    expression_id=expression_id,
                    shown_at=now,
                    interval_days=FIRST_INTERVAL_DAYS,
                    next_review_at=now + timedelta(days=FIRST_INTERVAL_DAYS),
                )
                for expression_id in expression_ids
            ]
            self._session.add_all(rows)
            await self._session.flush()
            for row in rows:
                await self._session.refresh(row)
            return rows

    async def get_due(
        self, user_id: int, *, now: datetime, limit: int
    ) -> list[ExpressionProgress]:
        """Progress rows whose review is due, oldest due first."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(ExpressionProgress)
                .where(
                    ExpressionProgress.user_id == user_id,
                    ExpressionProgress.next_review_at.is_not(None),
                    ExpressionProgress.next_review_at <= now,
                )
                .order_by(ExpressionProgress.next_review_at, ExpressionProgress.id)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def update_srs(
        self, progress: ExpressionProgress, srs_update: SrsUpdate
    ) -> ExpressionProgress:
        """Persist the scheduling state computed for one answer."""
        async with wrap_storage_errors():
            progress.review_count = srs_update.review_count
            progress.ease_factor = srs_update.ease_factor
            progress.interval_days = srs_update.interval_days
            progress.next_review_at = srs_update.next_review_at
            progress.last_reviewed_at = srs_update.answered_at
            await self._session.flush()
            await self._session.refresh(progress)
            return progress

    async def mark_completed(self, user_id: int, expression_ids: list[int]) -> int:
        """Set ``completed_at`` for not-yet-completed rows; return count."""
        if not expression_ids:
            return 0
        async with wrap_storage_errors():
            result = await self._session.execute(
                update(ExpressionProgress)
                .where(
                    ExpressionProgress.user_id == user_id,
                    ExpressionProgress.expression_id.in_(expression_ids),
                    ExpressionProgress.completed_at.is_(None),
                )
                .values(completed_at=func.now())
                .returning(ExpressionProgress.id)
            )
            completed_ids = result.all()
            return len(completed_ids)
