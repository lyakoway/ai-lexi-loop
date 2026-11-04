"""create profiles table

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.models.profile import ConstraintNames

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("native_language", sa.String(length=8), nullable=False),
        sa.Column("target_language", sa.String(length=8), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("learning_goal", sa.String(length=32), nullable=False),
        sa.Column(
            "topics",
            postgresql.ARRAY(sa.String(length=32)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("preferred_accent", sa.String(length=4), nullable=False),
        sa.Column("daily_study_minutes", sa.Integer(), nullable=False),
        sa.Column("new_items_per_day", sa.Integer(), nullable=False),
        sa.Column("lesson_time", sa.Time(), nullable=False),
        sa.Column("review_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name=op.f("uq_profiles_user_id")),
        sa.CheckConstraint(
            "level IN ('unknown', 'A1', 'A2', 'B1', 'B2', 'C1', 'C2')",
            name=ConstraintNames.LEVEL,
        ),
        sa.CheckConstraint(
            "daily_study_minutes BETWEEN 5 AND 120",
            name=ConstraintNames.MINUTES,
        ),
        sa.CheckConstraint(
            "new_items_per_day BETWEEN 1 AND 20",
            name=ConstraintNames.ITEMS,
        ),
    )


def downgrade() -> None:
    op.drop_table("profiles")
