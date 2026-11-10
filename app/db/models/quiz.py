"""Quiz evaluation telemetry: one row per graded free-form answer.

Metrics only, deliberately without the answer text or the model's raw
output: the user's words are conversation content and are never stored
in the telemetry contour. ``is_correct`` is ``NULL`` when the answer
could not be graded at all (LLM failure, invalid output) — the row then
carries the ``error_kind`` instead, so failed evaluations are still
counted.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QuizEvaluation(Base):
    """Best-effort telemetry of one quiz answer evaluation."""

    __tablename__ = "quiz_evaluations"
    __table_args__ = (Index("ix_quiz_evaluations_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    expression_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("expressions.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The evaluation verdict: grammar | vocabulary | usage | none; NULL
    # when no evaluation was produced.
    error_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # Why an evaluation did not happen: LlmError subclass name,
    # "invalid_output", ... NULL for graded answers.
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"QuizEvaluation(id={self.id!r}, user_id={self.user_id!r}, "
            f"expression_id={self.expression_id!r}, is_correct={self.is_correct!r})"
        )
