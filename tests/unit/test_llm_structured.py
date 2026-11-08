"""Unit tests for structured output validation and the repair attempt."""

from typing import Any
from unittest.mock import AsyncMock

from pydantic import BaseModel

from app.integrations.llm.client import StructuredClient, parse_structured
from app.integrations.llm.exceptions import (
    LlmOutputFormatError,
    LlmTransportError,
)
from app.integrations.llm.schemas import LlmRequest, LlmResponse


class Answer(BaseModel):
    explanation: str
    examples: list[str]


def make_response(content: str) -> LlmResponse:
    return LlmResponse(
        content=content,
        model="test-model",
        provider="openai_compatible",
    )


def make_inner(contents: list[str]) -> AsyncMock:
    """A fake LlmClient answering with the given contents in order."""
    inner = AsyncMock()
    inner.complete.side_effect = [make_response(content) for content in contents]
    return inner


def make_request() -> LlmRequest:
    return LlmRequest(messages=[], prompt_version="explain_v1")


async def test_valid_json_parses_into_schema() -> None:
    inner = make_inner(['{"explanation": "hi", "examples": ["a"]}'])
    client = StructuredClient(inner=inner)

    result = await client.complete_structured(make_request(), Answer)

    assert result.explanation == "hi"
    inner.complete.assert_awaited_once()


async def test_markdown_fence_is_tolerated() -> None:
    inner = make_inner(['```json\n{"explanation": "hi", "examples": []}\n```'])
    client = StructuredClient(inner=inner)

    result = await client.complete_structured(make_request(), Answer)

    assert result.explanation == "hi"


async def test_invalid_json_triggers_single_repair() -> None:
    inner = make_inner(["not json at all", '{"explanation": "fixed", "examples": []}'])
    client = StructuredClient(inner=inner)

    result = await client.complete_structured(make_request(), Answer)

    assert result.explanation == "fixed"
    assert inner.complete.await_count == 2
    repair_request = inner.complete.await_args_list[1].args[0]
    repair_messages = repair_request.messages
    assert repair_messages[-1].role == "user"
    assert repair_messages[-2].role == "assistant"
    # The repair call is distinguishable in telemetry.
    assert repair_request.prompt_version == "explain_v1+repair"


async def test_schema_mismatch_triggers_repair() -> None:
    inner = make_inner(
        [
            '{"explanation": "hi"}',  # missing required "examples"
            '{"explanation": "hi", "examples": ["ok"]}',
        ]
    )
    client = StructuredClient(inner=inner)

    result = await client.complete_structured(make_request(), Answer)

    assert result.examples == ["ok"]
    assert inner.complete.await_count == 2


async def test_final_failure_raises_output_format_error() -> None:
    inner = make_inner(["garbage", "still garbage"])
    client = StructuredClient(inner=inner)

    try:
        await client.complete_structured(make_request(), Answer)
        raise AssertionError("expected LlmOutputFormatError")
    except LlmOutputFormatError:
        pass
    assert inner.complete.await_count == 2  # exactly one repair, no more


async def test_transport_error_is_not_repaired() -> None:
    """Content repair never retries transport-level failures."""
    inner = AsyncMock()
    inner.complete.side_effect = LlmTransportError("provider unreachable")
    client = StructuredClient(inner=inner)

    try:
        await client.complete_structured(make_request(), Answer)
        raise AssertionError("expected LlmTransportError")
    except LlmTransportError:
        pass
    assert inner.complete.await_count == 1


def test_parse_structured_rejects_non_json() -> None:
    import pytest

    with pytest.raises(LlmOutputFormatError):
        parse_structured("nope", Answer)


def test_parse_structured_rejects_wrong_schema() -> None:
    import pytest

    with pytest.raises(LlmOutputFormatError):
        parse_structured('{"wrong": 1}', Answer)


def test_observing_client_protocol_satisfied(fake: Any = None) -> None:
    """The composed client implements the StructuredLlmClient protocol."""
    from app.integrations.llm.client import StructuredLlmClient

    client = StructuredClient(inner=make_inner(["{}"]))
    assert isinstance(client, StructuredLlmClient)
