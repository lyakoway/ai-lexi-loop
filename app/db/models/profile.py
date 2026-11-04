"""Learning profile model: one per user, created during onboarding."""

from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConstraintNames:
    """Logical constraint names passed through the metadata naming convention.

    The convention in :mod:`app.db.base` renders them as
    ``ck_profiles_<name>``; migrations pass the same short names, so the
    model and the database always agree.
    """

    LEVEL = "level_valid"
    MINUTES = "daily_study_minutes_range"
    ITEMS = "new_items_per_day_range"


class Profile(Base):
    """Persistent learning settings of a single user.

    One-to-one with :class:`~app.db.models.User`. ``topics`` is stored as
    a PostgreSQL array: a flat tag list with no per-topic attributes does
    not justify a join table, while the array still allows containment
    queries (``topics @> ARRAY[...]``) when lesson selection appears.
    """

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(
            "level IN ('unknown', 'A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name=ConstraintNames.LEVEL,
        ),
        CheckConstraint(
            "daily_study_minutes BETWEEN 5 AND 120",
            name=ConstraintNames.MINUTES,
        ),
        CheckConstraint(
            "new_items_per_day BETWEEN 1 AND 20", name=ConstraintNames.ITEMS
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
        unique=True,
        nullable=False,
    )
    native_language: Mapped[str] = mapped_column(String(8), nullable=False)
    target_language: Mapped[str] = mapped_column(String(8), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    learning_goal: Mapped[str] = mapped_column(String(32), nullable=False)
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)),
        nullable=False,
        server_default="{}",
    )
    preferred_accent: Mapped[str] = mapped_column(String(4), nullable=False)
    daily_study_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    new_items_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    lesson_time: Mapped[time] = mapped_column(Time(), nullable=False)
    review_time: Mapped[time] = mapped_column(Time(), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"Profile(id={self.id!r}, user_id={self.user_id!r})"
