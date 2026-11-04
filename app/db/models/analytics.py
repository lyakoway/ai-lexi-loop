"""Product analytics events.

Events are written by application services in the same transaction as
the business change they describe. ``properties`` holds only structured
scalar values (ids, counts, flags) curated by the services — never raw
user messages, tokens or other sensitive payloads.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnalyticsEvent(Base):
    """One product event performed by a user through some channel."""

    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("ix_analytics_events_name_occurred_at", "event_name", "occurred_at"),
        Index("ix_analytics_events_user_occurred_at", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    properties: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        server_default="{}",
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"AnalyticsEvent(id={self.id!r}, user_id={self.user_id!r}, "
            f"event_name={self.event_name!r}, source={self.source!r})"
        )
