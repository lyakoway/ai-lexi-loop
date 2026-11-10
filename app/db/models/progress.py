"""Per-user expression progress.

For the lesson MVP this records the fact that an expression was served to
the user (``shown_at``) and whether the user finished the card's lesson
(``completed_at``). The stage-4 spaced repetition will extend this table
with scheduling columns (review counter, next review time) instead of
replacing it, which is why the fact of serving lives in its own table
with a unique (user, expression) pair rather than being derived from
lesson logs.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.srs import FIRST_INTERVAL_DAYS, INITIAL_EASE_FACTOR


class ExpressionProgress(Base):
    """One row per (user, expression) ever served to that user."""

    __tablename__ = "expression_progress"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "expression_id",
            name="uq_expression_progress_user_id_expression_id",
        ),
    )

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
    shown_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Spaced repetition state (simplified SM-2, see app.domain.srs).
    # ``next_review_at`` is scheduled as soon as the card is served, so
    # even a card from an abandoned lesson returns via /review.
    review_count: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
    )
    ease_factor: Mapped[float] = mapped_column(
        Float,
        server_default=str(INITIAL_EASE_FACTOR),
        nullable=False,
    )
    interval_days: Mapped[int] = mapped_column(
        Integer,
        server_default=str(FIRST_INTERVAL_DAYS),
        nullable=False,
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # How many times the card was reset by a "forgot"/wrong answer —
    # drives the weekly "top forgotten" report. Maintained by the shared
    # ``update_srs`` write path, so reviews and quiz answers count alike.
    lapse_count: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"ExpressionProgress(id={self.id!r}, user_id={self.user_id!r}, "
            f"expression_id={self.expression_id!r})"
        )
