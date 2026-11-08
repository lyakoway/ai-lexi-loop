"""Shared test helpers."""

from typing import Any

from app.core.config import Settings
from app.integrations.llm.schemas import LlmRequest, LlmResponse

# A port that is closed on every machine; used to simulate an unavailable
# database without waiting for a timeout.
UNREACHABLE_DATABASE_URL = "postgresql://user:pass@127.0.0.1:1/unreachable"


def make_settings(**overrides: Any) -> Settings:
    """Build isolated test settings.

    ``_env_file=None`` guarantees a developer's ``.env`` never leaks into
    tests. Defaults describe a harmless test environment.
    """
    defaults: dict[str, Any] = {
        "database_url": "postgresql://user:pass@localhost:5432/ai_lexi_loop_test",
        "testing": True,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


class FakeLlmClient:
    """Configurable stand-in for the structured LLM client (no network).

    Mirrors ``StructuredClient`` behaviour: ``complete_structured``
    parses the preset content against the requested schema, so invalid
    JSON raises ``LlmOutputFormatError`` exactly like the real stack.
    """

    def __init__(
        self,
        contents: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.requests: list[LlmRequest] = []
        self._contents = list(contents or [])
        self._error = error

    async def complete(self, request: LlmRequest) -> LlmResponse:
        from app.integrations.llm.schemas import LlmResponse as _LlmResponse

        if self._error is not None:
            raise self._error
        content = self._contents.pop(0) if self._contents else "{}"
        return _LlmResponse(content=content, model="fake-model", provider="fake")

    async def complete_structured(self, request: LlmRequest, schema: Any) -> Any:
        from app.integrations.llm.client import parse_structured

        self.requests.append(request)
        response = await self.complete(request)
        return parse_structured(response.content, schema)
