"""AI tutor chat models: conversations and messages (Stage 6.4).

Isolation: every conversation belongs to exactly one user; messages are
reachable only through their conversation. The pair link is enforced at
the schema level: an assistant message references the user message it
answers via a composite FK ``(in_reply_to_message_id, conversation_id)``,
which guarantees the referenced message belongs to the same conversation.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatConstraintNames:
    """Logical constraint names rendered by the metadata naming convention."""

    LEVEL = "level_valid"
    STATUS = "status_valid"
    ROLE = "role_valid"
    ROLE_LINK = "role_link_valid"


class TutorConversation(Base):
    """One tutoring dialog of a single user.

    ``status`` is ``active`` while the dialog can accept new messages and
    ``closed`` afterwards; the partial unique index allows only one
    active dialog per user (enforced by PostgreSQL, see the index below).
    ``generation_started_at``/``generation_claim_id`` are claim columns
    reserved for the generation orchestration implemented in Stage 6.4.2.
    """

    __tablename__ = "tutor_conversations"
    __table_args__ = (
        CheckConstraint(
            "level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name=ChatConstraintNames.LEVEL,
        ),
        CheckConstraint(
            "status IN ('active', 'closed')",
            name=ChatConstraintNames.STATUS,
        ),
        Index(
            "uq_tutor_conversations_active_user_id",
            "user_id",
            unique=True,
            postgresql_where=sa_text("status = 'active'"),
        ),
        Index("ix_tutor_conversations_user_status", "user_id", "status"),
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
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(32), nullable=True)
    situation: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), server_default="active", nullable=False
    )
    message_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    generation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    generation_claim_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    last_generation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"TutorConversation(id={self.id!r}, user_id={self.user_id!r}, "
            f"status={self.status!r})"
        )


class TutorMessage(Base):
    """One message of a tutoring dialog (user or assistant).

    ``in_reply_to_message_id`` is set for assistant messages and points to
    the user message being answered. The composite FK
    ``(in_reply_to_message_id, conversation_id) → (id, conversation_id)``
    guarantees the referenced user message belongs to the same
    conversation; the CHECK constraint guarantees the role/link pairing
    (a user message never has a link, an assistant message always has
    one). ``client_request_id`` carries the Web client's Idempotency-Key
    and is unique per conversation for user messages.
    """

    __tablename__ = "tutor_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name=ChatConstraintNames.ROLE,
        ),
        CheckConstraint(
            "(role = 'user' AND in_reply_to_message_id IS NULL) "
            "OR (role = 'assistant' AND in_reply_to_message_id IS NOT NULL)",
            name=ChatConstraintNames.ROLE_LINK,
        ),
        UniqueConstraint(
            "id",
            "conversation_id",
            name="uq_tutor_messages_id_conversation_id",
        ),
        ForeignKeyConstraint(
            ["in_reply_to_message_id", "conversation_id"],
            ["tutor_messages.id", "tutor_messages.conversation_id"],
            ondelete="CASCADE",
            use_alter=True,
            name="fk_tutor_messages_reply_target",
        ),
        Index(
            "uq_tutor_messages_conversation_client_request_id",
            "conversation_id",
            "client_request_id",
            unique=True,
            postgresql_where=sa_text("role = 'user' AND client_request_id IS NOT NULL"),
        ),
        Index(
            "ix_tutor_messages_conversation_created",
            "conversation_id",
            "created_at",
        ),
        Index("ix_tutor_messages_in_reply_to", "in_reply_to_message_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tutor_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    in_reply_to_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"TutorMessage(id={self.id!r}, conversation_id={self.conversation_id!r}, "
            f"role={self.role!r})"
        )
