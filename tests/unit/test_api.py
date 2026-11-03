"""Tests for the FastAPI application factory and its endpoints."""

from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from tests.conftest import UNREACHABLE_DATABASE_URL, make_settings


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator[tuple[AsyncClient, FastAPI]]:
    """Client for an app whose database is unreachable.

    Returns the app too, so tests can inspect ``app.state`` or mount extra
    routes. ``raise_app_exceptions=False`` mirrors a real ASGI server,
    which logs unexpected errors after sending the 500 response.
    """
    app = create_app(make_settings(database_url=UNREACHABLE_DATABASE_URL))
    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


async def test_create_app_registers_health_routes() -> None:
    """The health and readiness endpoints must be part of the API contract.

    Checked through the public OpenAPI schema rather than ``app.routes``
    internals, whose shape differs across FastAPI versions.
    """
    app = create_app(make_settings())
    openapi_paths = set(app.openapi()["paths"])
    assert {"/health", "/ready"}.issubset(openapi_paths)


async def test_health_returns_ok(
    app_client: tuple[AsyncClient, FastAPI],
) -> None:
    client, _ = app_client
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_reports_database_failure(
    app_client: tuple[AsyncClient, FastAPI],
) -> None:
    """With the database down, readiness must fail and say so."""
    client, _ = app_client
    response = await client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["database"] == "down"
    assert body["bot"] == "disabled"


async def test_unhandled_error_returns_no_internals(
    app_client: tuple[AsyncClient, FastAPI],
) -> None:
    """Unexpected exceptions must not leak stack traces to clients."""
    client, app = app_client

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret internal detail" not in response.text


async def test_lifespan_creates_and_disposes_engine() -> None:
    """The engine must exist while the app runs and disappear after."""
    app = create_app(make_settings(database_url=UNREACHABLE_DATABASE_URL))
    async with LifespanManager(app):
        assert app.state.engine is not None
    assert app.state.engine is None


async def test_bot_runner_is_not_started_in_test_mode() -> None:
    """TESTING=true must keep the app alive without Telegram polling."""
    app = create_app(make_settings(telegram_bot_token="123:abc", testing=True))
    async with LifespanManager(app):
        assert app.state.bot_runner is None


async def test_bot_runner_starts_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a token and TESTING=false the supervised runner must start."""

    class StubRunner:
        instances: ClassVar[list["StubRunner"]] = []
        status = "running"

        def __init__(self, bot: Any, dispatcher: Any) -> None:
            self.started = False
            StubRunner.instances.append(self)

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.started = False

    monkeypatch.setattr("app.api.app.BotRunner", StubRunner)

    app = create_app(make_settings(telegram_bot_token="123:abc", testing=False))
    async with LifespanManager(app):
        runner: Any = app.state.bot_runner
        assert runner is not None and runner.started
        assert StubRunner.instances, "runner must be constructed by the lifespan"
