"""personal expression drafts and catalogue ownership

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-04

Personal AI drafts live in user_generated_expressions until the user
accepts them. Acceptance copies the card into the expressions table
with owner_user_id set (NULL = shared catalogue), so the existing
progress/SRS machinery keeps working unchanged.

The global UNIQUE (language, text) constraint is replaced with two
partial unique indexes: shared cards are unique per
(language, lower(text)); personal cards per (owner, language,
lower(text)) — different users can own the same expression, nobody can
duplicate a card, case-insensitively.

DOWNGRADE IS DESTRUCTIVE: it deletes every personal card
(owner_user_id IS NOT NULL) before restoring the old global unique
constraint — personal cards cannot exist without the owner column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "expressions",
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_expressions_owner_user_id_users"),
        "expressions",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_expressions_owner_user_id"),
        "expressions",
        ["owner_user_id"],
        unique=False,
    )
    # Replace the global uniqueness with owner-scoped, case-insensitive
    # rules (see the docstring). Existing data already satisfies both.
    op.drop_constraint("uq_expressions_language_text", "expressions", type_="unique")
    op.create_index(
        "uq_expressions_shared_language_lower_text",
        "expressions",
        ["language", sa.text("lower(text)")],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )
    op.create_index(
        "uq_expressions_personal_owner_language_lower_text",
        "expressions",
        ["owner_user_id", "language", sa.text("lower(text)")],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )

    op.create_table(
        "user_generated_expressions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("native_language", sa.String(length=8), nullable=False),
        sa.Column("text", sa.String(length=128), nullable=False),
        sa.Column("translation", sa.String(length=256), nullable=False),
        sa.Column("ipa", sa.String(length=64), server_default="", nullable=False),
        sa.Column(
            "phonetic_hint_ru",
            sa.String(length=128),
            server_default="",
            nullable=False,
        ),
        sa.Column("example", sa.Text(), nullable=False),
        sa.Column("example_translation", sa.Text(), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.Column(
            "source",
            sa.String(length=16),
            server_default="ai_generated",
            nullable=False,
        ),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("accepted_expression_id", sa.BigInteger(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_generated_expressions")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_generated_expressions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_expression_id"],
            ["expressions.id"],
            name=op.f(
                "fk_user_generated_expressions_accepted_expression_id_expressions"
            ),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "user_id",
            "text",
            name=op.f("uq_user_generated_expressions_user_id_text"),
        ),
        sa.CheckConstraint(
            "level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name="level_valid",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'accepted', 'rejected')",
            name="status_valid",
        ),
    )
    op.create_index(
        "ix_user_generated_expressions_user_status",
        "user_generated_expressions",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_generated_expressions_user_status",
        table_name="user_generated_expressions",
    )
    op.drop_table("user_generated_expressions")

    # Personal cards would silently become shared if the owner column
    # were dropped without cleaning them up first.
    op.execute("DELETE FROM expressions WHERE owner_user_id IS NOT NULL")
    op.drop_index(op.f("ix_expressions_owner_user_id"), table_name="expressions")
    op.drop_constraint(
        op.f("fk_expressions_owner_user_id_users"),
        "expressions",
        type_="foreignkey",
    )
    op.drop_column("expressions", "owner_user_id")
