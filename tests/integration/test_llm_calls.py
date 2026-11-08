"""Integration tests for llm_calls telemetry on a real PostgreSQL."""

import os
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from app.db.models import LlmCall
from app.db.session import SessionFactory, session_scope
from app.integrations.llm import build_llm_client
from app.integrations.llm.exceptions import LlmProviderError
from app.integrations.llm.recorder import LlmCallRecorder
from app.integrations.llm.schemas import LlmMessage, LlmRequest
from tests.conftest import make_settings
from tests.integration.conftest import TEST_DATABASE_URL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


def ok_handler(content: str = "hi") -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 11,
                    "total_tokens": 18,
                },
            },
        )

    return handler


def make_composed_client(session_factory: SessionFactory, handler: Any) -> Any:
    settings = make_settings(
        database_url=TEST_DATABASE_URL,
        llm_enabled=True,
        llm_base_url="https://llm.example/v1",
        llm_api_key="sk-integration",
        llm_model="test-model",
        llm_retry_attempts=0,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return build_llm_client(settings, http, LlmCallRecorder(session_factory))


def make_request() -> LlmRequest:
    return LlmRequest(
        messages=[LlmMessage(role="user", content="secret user text")],
        prompt_version="explain_v1",
    )


async def fetch_calls(
    session_factory: SessionFactory,
) -> list[LlmCall]:
    async with session_scope(session_factory) as session:
        return list(
            (await session.execute(select(LlmCall).order_by(LlmCall.id)))
            .scalars()
            .all()
        )


async def clear_llm_calls(session_factory: SessionFactory) -> None:
    """Telemetry is global; each test starts from an empty table."""
    async with session_scope(session_factory) as session:
        await session.execute(text("DELETE FROM llm_calls"))


async def test_success_call_is_recorded(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    await clear_llm_calls(session_factory)
    client = make_composed_client(session_factory, ok_handler())

    response = await client.complete(make_request())

    assert response.content == "hi"
    rows = await fetch_calls(session_factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "openai_compatible"
    assert row.model == "test-model"
    assert row.prompt_version == "explain_v1"
    assert row.status == "ok"
    assert row.input_tokens == 7
    assert row.output_tokens == 11
    assert row.total_tokens == 18
    assert row.error_type is None
    assert row.estimated_cost is None  # unknown price → NULL, never made up
    assert row.latency_ms >= 0


async def test_error_call_is_recorded(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    await clear_llm_calls(session_factory)
    client = make_composed_client(
        session_factory,
        lambda request: httpx.Response(401, text="bad key"),
    )

    with pytest.raises(LlmProviderError):
        await client.complete(make_request())

    rows = await fetch_calls(session_factory)
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_type == "LlmProviderError"
    assert rows[0].input_tokens is None


async def test_no_prompt_or_answer_text_is_stored(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """The telemetry row must not contain any content."""
    await clear_llm_calls(session_factory)
    client = make_composed_client(session_factory, ok_handler("private reply"))

    await client.complete(make_request())

    rows = await fetch_calls(session_factory)
    stored = rows[0].__dict__
    for forbidden in ("secret user text", "private reply", "sk-integration"):
        assert not any(
            forbidden in str(value) for value in stored.values()
        ), f"{forbidden!r} leaked into llm_calls"


async def test_recorder_database_failure_does_not_break_answer(
    migrated_db: None,
) -> None:
    """A dead database must not turn a model answer into an error."""
    from app.db.session import build_engine, build_session_factory

    engine = build_engine(
        make_settings(database_url="postgresql://u:p@127.0.0.1:1/dead")
    )
    dead_factory = build_session_factory(engine)
    client = make_composed_client(dead_factory, ok_handler())

    response = await client.complete(make_request())

    assert response.content == "hi"
    await engine.dispose()


async def test_llm_calls_table_shape(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        columns = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'llm_calls'"
                    )
                )
            ).all()
        }
    assert {
        "id",
        "provider",
        "model",
        "prompt_version",
        "status",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_cost",
        "error_type",
        "request_id",
        "created_at",
    } == columns
