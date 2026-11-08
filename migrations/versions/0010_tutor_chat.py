"""create tutor chat conversations and messages

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08

One active conversation per user (partial unique index). Messages are
linked pairwise: an assistant message references the user message it
answers via the composite FK (in_reply_to_message_id, conversation_id)
→ tutor_messages (id, conversation_id), which guarantees the target
belongs to the same conversation. The claim columns
(generation_started_at / generation_claim_id / last_generation_at) are
storage only — the claim lifecycle is implemented by ChatService in
Stage 6.4.2.

DOWNGRADE drops both tables with all their history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text as sa_text

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tutor_conversations",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("native_language", sa.String(length=8), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=True),
        sa.Column("situation", sa.String(length=300), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_claim_id", sa.Uuid(), nullable=True),
        sa.Column("last_generation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tutor_conversations")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tutor_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name="level_valid",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'closed')",
            name="status_valid",
        ),
    )
    op.create_index(
        "uq_tutor_conversations_active_user_id",
        "tutor_conversations",
        ["user_id"],
        unique=True,
        postgresql_where=sa_text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_tutor_conversations_user_status"),
        "tutor_conversations",
        ["user_id", "status"],
        unique=False,
    )

    # Messages: the composite self-referential FK is added after the
    # table (its referenced UNIQUE (id, conversation_id) exists by then).
    op.create_table(
        "tutor_messages",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=True),
        sa.Column("in_reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tutor_messages")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["tutor_conversations.id"],
            name=op.f("fk_tutor_messages_conversation_id_tutor_conversations"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "conversation_id",
            name=op.f("uq_tutor_messages_id_conversation_id"),
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        sa.CheckConstraint(
            "(role = 'user' AND in_reply_to_message_id IS NULL) "
            "OR (role = 'assistant' AND in_reply_to_message_id IS NOT NULL)",
            name="role_link_valid",
        ),
    )
    op.create_index(
        "uq_tutor_messages_conversation_client_request_id",
        "tutor_messages",
        ["conversation_id", "client_request_id"],
        unique=True,
        postgresql_where=sa_text("role = 'user' AND client_request_id IS NOT NULL"),
    )
    op.create_index(
        op.f("ix_tutor_messages_conversation_created"),
        "tutor_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tutor_messages_in_reply_to"),
        "tutor_messages",
        ["in_reply_to_message_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_tutor_messages_reply_target",
        "tutor_messages",
        "tutor_messages",
        ["in_reply_to_message_id", "conversation_id"],
        ["id", "conversation_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_tutor_messages_reply_target",
        "tutor_messages",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_tutor_messages_conversation_client_request_id",
        table_name="tutor_messages",
    )
    op.drop_index(
        op.f("ix_tutor_messages_conversation_created"), table_name="tutor_messages"
    )
    op.drop_index(op.f("ix_tutor_messages_in_reply_to"), table_name="tutor_messages")
    op.drop_table("tutor_messages")
    op.drop_index(
        "uq_tutor_conversations_active_user_id", table_name="tutor_conversations"
    )
    op.drop_index(
        op.f("ix_tutor_conversations_user_status"), table_name="tutor_conversations"
    )
    op.drop_table("tutor_conversations")
