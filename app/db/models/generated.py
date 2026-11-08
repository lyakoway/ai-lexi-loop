"""AI-generated expression drafts owned by a single user.

Drafts live outside the shared catalogue until the user explicitly
accepts them; acceptance copies the card into ``expressions`` with the
user as its owner (see the acceptance flow in the generation service).
Rejected and draft rows never enter lessons, reviews or the SRS.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GeneratedConstraintNames:
    """Logical constraint names rendered by the naming convention."""

    LEVEL = "level_valid"
    STATUS = "status_valid"


class UserGeneratedExpression(Base):
    """One AI-generated expression draft of one user."""

    __tablename__ = "user_generated_expressions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "text",
            name="uq_user_generated_expressions_user_id_text",
        ),
        CheckConstraint(
            "level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name=GeneratedConstraintNames.LEVEL,
        ),
        CheckConstraint(
            "status IN ('draft', 'accepted', 'rejected')",
            name=GeneratedConstraintNames.STATUS,
        ),
        Index(
            "ix_user_generated_expressions_user_status",
            "user_id",
            "status",
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
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    native_language: Mapped[str] = mapped_column(String(8), nullable=False)
    text: Mapped[str] = mapped_column(String(128), nullable=False)
    translation: Mapped[str] = mapped_column(String(256), nullable=False)
    ipa: Mapped[str] = mapped_column(String(64), server_default="", nullable=False)
    phonetic_hint_ru: Mapped[str] = mapped_column(
        String(128), server_default="", nullable=False
    )
    example: Mapped[str] = mapped_column(Text(), nullable=False)
    example_translation: Mapped[str] = mapped_column(Text(), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), server_default="ai_generated", nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    accepted_expression_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("expressions.id", ondelete="SET NULL"),
        nullable=True,
    )
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
        return (
            f"UserGeneratedExpression(id={self.id!r}, user_id={self.user_id!r}, "
            f"status={self.status!r}, text={self.text!r})"
        )
