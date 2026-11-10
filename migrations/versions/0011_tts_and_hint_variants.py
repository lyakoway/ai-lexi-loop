"""tts cache/telemetry tables; accent hints and is_difficult on expressions

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-08

- expressions: accent-specific Russian hints (phonetic_hint_ru_us / _uk,
  NULL falls back to the base phonetic_hint_ru) and is_difficult flag
  (difficult words keep their hint after it fades for the rest).
  Existing rows are backfilled: us = uk = base hint; the schedule row
  gets a genuinely different UK hint (US/UK pronunciations differ).
- tts_audio: audio cache metadata; files live on disk, not in the DB.
- tts_calls: TTS call telemetry (metrics only, no content).

DOWNGRADE drops the accent hint columns (base phonetic_hint_ru is
kept) and both new tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "expressions",
        sa.Column("phonetic_hint_ru_us", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "expressions",
        sa.Column("phonetic_hint_ru_uk", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "expressions",
        sa.Column(
            "is_difficult",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    # Backfill: accent hints start as copies of the base hint; curate
    # per-word differences in the data afterwards.
    op.execute(
        "UPDATE expressions SET phonetic_hint_ru_us = phonetic_hint_ru, "
        "phonetic_hint_ru_uk = phonetic_hint_ru"
    )
    # Ship a real US/UK difference for the schedule seed.
    op.execute(
        "UPDATE expressions SET phonetic_hint_ru_uk = 'лэтс шэдьюл э мИтинг' "
        "WHERE text = 'Let''s schedule a meeting'"
    )

    op.create_table(
        "tts_audio",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_version", sa.String(length=64), nullable=False),
        sa.Column("voice", sa.String(length=64), nullable=False),
        sa.Column("accent", sa.String(length=8), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tts_audio")),
        sa.UniqueConstraint("cache_key", name=op.f("uq_tts_audio_cache_key")),
    )
    op.create_table(
        "tts_calls",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("voice", sa.String(length=64), nullable=False),
        sa.Column("accent", sa.String(length=8), nullable=False),
        sa.Column(
            "cache_hit", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tts_calls")),
    )


def downgrade() -> None:
    op.drop_table("tts_calls")
    op.drop_table("tts_audio")
    # DESTRUCTIVE for the accent hint columns: the base hint is kept,
    # accent-specific variants and the difficulty flag are dropped.
    op.drop_column("expressions", "is_difficult")
    op.drop_column("expressions", "phonetic_hint_ru_uk")
    op.drop_column("expressions", "phonetic_hint_ru_us")
