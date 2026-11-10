"""Typed exceptions of the TTS integration.

Mirrors the LLM exception levels: transport (retryable) vs provider
(HTTP; retryable for 429/5xx) vs configuration (never retried).
Messages never contain API keys or secrets.
"""


class TtsError(Exception):
    """Base class of all TTS integration errors."""


class TtsTransportError(TtsError):
    """Network-level failure: connect error, timeout."""


class TtsProviderError(TtsError):
    """The provider returned an HTTP error response."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code == 429 or status_code >= 500


class TtsConfigurationError(TtsError):
    """The TTS integration is misconfigured; never retried."""
