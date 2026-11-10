"""TTS (text-to-speech) provider integrations.

Mirrors the LLM integrations layout: a provider protocol, an
OpenAI-compatible implementation with retries, and best-effort
call telemetry. Nothing here knows about Telegram or the database
cache (that lives in ``app.services.tts``).
"""

from app.integrations.tts.exceptions import (
    TtsConfigurationError,
    TtsError,
    TtsProviderError,
    TtsTransportError,
)
from app.integrations.tts.provider import NullTtsProvider, TtsProvider

__all__ = [
    "NullTtsProvider",
    "TtsConfigurationError",
    "TtsError",
    "TtsProvider",
    "TtsProviderError",
    "TtsTransportError",
]
