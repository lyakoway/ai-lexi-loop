"""add spaced repetition columns to expression_progress

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SRS_COLUMNS = (
    "last_reviewed_at",
    "next_review_at",
    "interval_days",
    "ease_factor",
    "review_count",
)


def upgrade() -> None:
    op.add_column(
        "expression_progress",
        sa.Column(
            "review_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "expression_progress",
        sa.Column(
            "ease_factor",
            sa.Float(),
            server_default="2.5",
            nullable=False,
        ),
    )
    op.add_column(
        "expression_progress",
        sa.Column(
            "interval_days",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "expression_progress",
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "expression_progress",
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill existing progress: every already served card becomes
    # reviewable one day after it was (last) studied. Rows keep their
    # shown_at/completed_at; only scheduling columns are added.
    op.execute("""
        UPDATE expression_progress
        SET next_review_at = COALESCE(completed_at, shown_at)
                            + interval '1 day',
            interval_days = 1,
            review_count = 0,
            ease_factor = 2.5
        WHERE next_review_at IS NULL
        """)


def downgrade() -> None:
    for column in _SRS_COLUMNS:
        op.drop_column("expression_progress", column)
