"""DTOs of the LLM client contract.

These models are provider-agnostic: any future provider implementation
adapts its native types to them, and business code (the future
TutorService) depends only on these.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MessageRole = Literal["system", "user", "assistant"]


class LlmMessage(BaseModel):
    """One chat message sent to the model."""

    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str


class LlmUsage(BaseModel):
    """Token accounting as reported by the provider (may be absent)."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class LlmRequest(BaseModel):
    """A single completion request.

    ``response_schema`` asks the provider for JSON matching the Pydantic
    model (used through ``complete_structured``); providers without a
    native JSON mode fall back to prompting for JSON in their adapter.
    """

    model_config = ConfigDict(frozen=True)

    messages: list[LlmMessage]
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_output_tokens: int | None = None
    response_schema: type[BaseModel] | None = None
    prompt_version: str = ""
    model: str | None = None  # None → the client's configured default


class LlmResponse(BaseModel):
    """A successful completion result."""

    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    provider: str
    usage: LlmUsage | None = None
    finish_reason: str | None = None
    request_id: str | None = None
