"""Typed exceptions of the LLM integration.

Four distinct levels, mirroring the transport/provider/format/validation
split required by the retry policy:

- :class:`LlmTransportError` — network problems (connection, timeout);
  retryable.
- :class:`LlmProviderError` — the provider answered with an HTTP error;
  retryable only for 429/5xx (see ``retryable``).
- :class:`LlmOutputFormatError` — the model answered, but the content is
  not valid JSON for the requested schema after the repair attempt.
- :class:`LlmConfigurationError` / :class:`LlmDisabledError` — local
  setup problems; never retried.

Messages must stay free of secrets: provider API keys travel in headers
and are never included in exception text.
"""


class LlmError(Exception):
    """Base class of all LLM integration errors."""


class LlmTransportError(LlmError):
    """Network-level failure: connect error, timeout, broken pipe."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class LlmProviderError(LlmError):
    """The provider returned an HTTP error response."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code == 429 or status_code >= 500


class LlmOutputFormatError(LlmError):
    """The model's content is not valid JSON for the requested schema."""


class LlmConfigurationError(LlmError):
    """The integration is misconfigured; never retried."""


class LlmDisabledError(LlmError):
    """The integration is switched off in settings."""
