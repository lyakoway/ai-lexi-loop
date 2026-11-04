"""Dictionary expression model: one learnable card of the target language."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExpressionConstraintNames:
    """Logical constraint names rendered by the metadata naming convention."""

    LEVEL = "level_valid"


class Expression(Base):
    """A dictionary entry shown to learners as a card.

    ``language`` is the target language code (matches
    ``Profile.target_language``); translations in the seed dictionary are
    Russian, matching the product's native-language focus. ``topic`` holds
    a key of :class:`~app.domain.profile.Topic`; it is validated on write
    by Pydantic schemas rather than by a DB CHECK, so the topic catalogue
    can grow without a migration on every addition.
    """

    __tablename__ = "expressions"
    __table_args__ = (
        UniqueConstraint(
            "language",
            "text",
            name="uq_expressions_language_text",
        ),
        CheckConstraint(
            "level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name=ExpressionConstraintNames.LEVEL,
        ),
        Index("ix_expressions_language_level", "language", "level"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    text: Mapped[str] = mapped_column(String(128), nullable=False)
    translation: Mapped[str] = mapped_column(String(256), nullable=False)
    ipa: Mapped[str] = mapped_column(String(64), nullable=False)
    phonetic_hint_ru: Mapped[str] = mapped_column(String(128), nullable=False)
    example: Mapped[str] = mapped_column(Text(), nullable=False)
    example_translation: Mapped[str] = mapped_column(Text(), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean(),
        server_default=func.true(),
        nullable=False,
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
            f"Expression(id={self.id!r}, language={self.language!r}, "
            f"text={self.text!r}, level={self.level!r}, topic={self.topic!r})"
        )
