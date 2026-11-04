"""move telegram identity to user_identities

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03

The internal users table stops storing the Telegram ID directly: every
existing link is carried over into user_identities with provider
'telegram', then users.telegram_user_id is dropped. Profiles, progress
and any other user data are bound to users.id and are untouched, so no
user loses anything; the unique (provider, provider_user_id) pair keeps
guaranteeing one account per Telegram ID.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provider_user_id", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identities")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_user_id",
            name=op.f("uq_user_identities_provider_provider_user_id"),
        ),
    )
    op.execute("""
        INSERT INTO user_identities (user_id, provider, provider_user_id)
        SELECT id, 'telegram', CAST(telegram_user_id AS VARCHAR(64))
        FROM users
        """)
    op.drop_index(op.f("ix_users_telegram_user_id"), table_name="users")
    op.drop_constraint(op.f("uq_users_telegram_user_id"), "users", type_="unique")
    op.drop_column("users", "telegram_user_id")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "telegram_user_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.execute("""
        UPDATE users
        SET telegram_user_id = CAST(i.provider_user_id AS BIGINT)
        FROM user_identities i
        WHERE i.user_id = users.id AND i.provider = 'telegram'
        """)
    op.alter_column("users", "telegram_user_id", nullable=False)
    op.create_unique_constraint(
        op.f("uq_users_telegram_user_id"),
        "users",
        ["telegram_user_id"],
    )
    op.create_index(
        op.f("ix_users_telegram_user_id"),
        "users",
        ["telegram_user_id"],
        unique=False,
    )
    op.drop_table("user_identities")
