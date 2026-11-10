"""quiz evaluations telemetry; quiz streak and report throttle on profiles

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-10

- profiles: quiz_success_streak (consecutive correct quiz answers; a CEFR
  promotion needs N in a row plus explicit confirmation) and
  last_forgotten_report_at (the "top forgotten" report is throttled to
  once per interval).
- expression_progress: lapse_count — how often a card was reset by a
  wrong answer; drives the weekly "top forgotten" report. Maintained by
  the shared update_srs path, so reviews and quiz answers count alike.
- quiz_evaluations: per-answer grading telemetry (is_correct, error_type,
  latency, error_kind). Metrics only: no answer text, no model output.

DOWNGRADE drops the new columns (lapse_count history is lost) and the
quiz_evaluations table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "quiz_success_streak",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "profiles",
        sa.Column(
            "last_forgotten_report_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "expression_progress",
        sa.Column(
            "lapse_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_table(
        "quiz_evaluations",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expression_id", sa.BigInteger(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("error_type", sa.String(length=16), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quiz_evaluations")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_quiz_evaluations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["expression_id"],
            ["expressions.id"],
            name=op.f("fk_quiz_evaluations_expression_id_expressions"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_quiz_evaluations_user_id",
        "quiz_evaluations",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_quiz_evaluations_user_id", table_name="quiz_evaluations")
    op.drop_table("quiz_evaluations")
    # DESTRUCTIVE: the accumulated lapse counters are lost.
    op.drop_column("expression_progress", "lapse_count")
    op.drop_column("profiles", "last_forgotten_report_at")
    op.drop_column("profiles", "quiz_success_streak")
