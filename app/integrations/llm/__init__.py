"""LLM provider integrations.

Business services depend on the :class:`~app.integrations.llm.client.LlmClient`
protocol only; concrete provider types never leave this package. The
composer in :mod:`app.integrations.llm.client` wires a provider together
with observability and structured-output validation.
"""

from app.integrations.llm.client import (
    LlmClient,
    ObservingLlmClient,
    StructuredLlmClient,
    build_llm_client,
)
from app.integrations.llm.exceptions import (
    LlmConfigurationError,
    LlmDisabledError,
    LlmError,
    LlmOutputFormatError,
    LlmProviderError,
    LlmTransportError,
)
from app.integrations.llm.schemas import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmUsage,
)

__all__ = [
    "LlmClient",
    "LlmConfigurationError",
    "LlmDisabledError",
    "LlmError",
    "LlmMessage",
    "LlmOutputFormatError",
    "LlmProviderError",
    "LlmRequest",
    "LlmResponse",
    "LlmTransportError",
    "LlmUsage",
    "ObservingLlmClient",
    "StructuredLlmClient",
    "build_llm_client",
]
