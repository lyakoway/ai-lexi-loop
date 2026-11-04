"""create expressions and expression_progress, seed dictionary

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.models.expression import ExpressionConstraintNames
from app.db.seed import SEED_EXPRESSIONS

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expressions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("text", sa.String(length=128), nullable=False),
        sa.Column("translation", sa.String(length=256), nullable=False),
        sa.Column("ipa", sa.String(length=64), nullable=False),
        sa.Column("phonetic_hint_ru", sa.String(length=128), nullable=False),
        sa.Column("example", sa.Text(), nullable=False),
        sa.Column("example_translation", sa.Text(), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expressions")),
        sa.UniqueConstraint(
            "language",
            "text",
            name=op.f("uq_expressions_language_text"),
        ),
        sa.CheckConstraint(
            "level IN ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name=ExpressionConstraintNames.LEVEL,
        ),
    )
    op.create_index(
        op.f("ix_expressions_language_level"),
        "expressions",
        ["language", "level"],
        unique=False,
    )

    # Seed the starter dictionary. ON CONFLICT DO NOTHING keeps the
    # insert idempotent even if rows with the same (language, text)
    # already exist.
    if SEED_EXPRESSIONS:
        rows = [entry.model_dump(mode="json") for entry in SEED_EXPRESSIONS]
        op.execute(
            postgresql.insert(
                sa.table(
                    "expressions",
                    sa.column("language", sa.String),
                    sa.column("text", sa.String),
                    sa.column("translation", sa.String),
                    sa.column("ipa", sa.String),
                    sa.column("phonetic_hint_ru", sa.String),
                    sa.column("example", sa.String),
                    sa.column("example_translation", sa.String),
                    sa.column("level", sa.String),
                    sa.column("topic", sa.String),
                )
            )
            .values(rows)
            .on_conflict_do_nothing(index_elements=["language", "text"])
        )

    op.create_table(
        "expression_progress",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expression_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "shown_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expression_progress")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_expression_progress_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["expression_id"],
            ["expressions.id"],
            name=op.f("fk_expression_progress_expression_id_expressions"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id",
            "expression_id",
            name=op.f("uq_expression_progress_user_id_expression_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("expression_progress")
    op.drop_index(op.f("ix_expressions_language_level"), table_name="expressions")
    op.drop_table("expressions")
