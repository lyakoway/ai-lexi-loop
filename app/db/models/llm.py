"""Telemetry rows for LLM calls.

Metrics only: never prompts, answers, user messages or any content —
see the recorder for the rationale. ``request_id`` is the project's own
correlation id (the same one as in logs), not personal data.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Identity,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LlmCall(Base):
    """One completed (or failed) LLM call with its timings and usage."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        # The two realistic query patterns: cost/latency analysis over
        # time and drill-down by model or prompt version.
        Index("ix_llm_calls_created_at", "created_at"),
        Index("ix_llm_calls_model_created_at", "model", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"LlmCall(id={self.id!r}, provider={self.provider!r}, "
            f"model={self.model!r}, status={self.status!r})"
        )
