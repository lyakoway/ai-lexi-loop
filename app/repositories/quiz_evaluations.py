"""Repository for the ``quiz_evaluations`` telemetry table.

Writes are best-effort by design: the caller (QuizService) swallows any
failure so telemetry problems can never turn a graded answer into an
error for the user. No answer text is stored — metrics only.
"""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import QuizEvaluation
from app.repositories.errors import wrap_storage_errors

ErrorType = Literal["grammar", "vocabulary", "usage", "none"]


class QuizEvaluationRepository:
    """Data access for quiz grading telemetry."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: int,
        expression_id: int,
        is_correct: bool | None,
        error_type: str | None,
        latency_ms: int,
        error_kind: str | None = None,
    ) -> None:
        """Insert one telemetry row (no commit: session_scope owns it)."""
        async with wrap_storage_errors():
            self._session.add(
                QuizEvaluation(
                    user_id=user_id,
                    expression_id=expression_id,
                    is_correct=is_correct,
                    error_type=error_type,
                    latency_ms=latency_ms,
                    error_kind=error_kind,
                )
            )
            await self._session.flush()

    async def count_for_user(self, user_id: int) -> int:
        """Number of telemetry rows for the user (used by tests/ops)."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(QuizEvaluation).where(QuizEvaluation.user_id == user_id)
            )
            return len(result.scalars().all())
