"""TTS models: audio cache metadata and call telemetry.

The audio itself lives on disk (``TtsAudio.file_path``), never in the
database. ``tts_calls`` is metrics-only: provider, voice, accent, cache
flag, latency, error kind — never the synthesized audio or the text.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TtsAudio(Base):
    """Cache metadata row for one synthesized phrase.

    The cache key is sha256(text + voice + accent + provider_version);
    ``provider_version`` is part of the key so a voice/model change
    invalidates cached audio.
    """

    __tablename__ = "tts_audio"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(String(500))
    provider: Mapped[str] = mapped_column(String(32))
    provider_version: Mapped[str] = mapped_column(String(64))
    voice: Mapped[str] = mapped_column(String(64))
    accent: Mapped[str] = mapped_column(String(8))
    file_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"TtsAudio(id={self.id!r}, cache_key={self.cache_key!r}, "
            f"voice={self.voice!r})"
        )


class TtsCall(Base):
    """One TTS call outcome (metrics only, no content)."""

    __tablename__ = "tts_calls"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    provider: Mapped[str] = mapped_column(String(32))
    voice: Mapped[str] = mapped_column(String(64))
    accent: Mapped[str] = mapped_column(String(8))
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"TtsCall(id={self.id!r}, provider={self.provider!r}, "
            f"voice={self.voice!r}, cache_hit={self.cache_hit!r})"
        )
