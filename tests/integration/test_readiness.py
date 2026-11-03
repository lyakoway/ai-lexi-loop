"""Integration tests for /ready against a real database."""

import os
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from tests.conftest import make_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


@pytest_asyncio.fixture
async def ready_client() -> Any:
    """Client for an app wired to the real test database."""
    from tests.integration.conftest import TEST_DATABASE_URL

    app = create_app(make_settings(database_url=TEST_DATABASE_URL))
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


async def test_ready_returns_ok_with_healthy_database(ready_client: Any) -> None:
    client, _ = ready_client
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "bot": "disabled"}


async def test_ready_fails_when_bot_runner_failed(ready_client: Any) -> None:
    """A crashed bot must turn readiness red even with a healthy database."""

    class FailedRunner:
        status = "failed"

    client, app = ready_client
    app.state.bot_runner = FailedRunner()

    response = await client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["bot"] == "failed"
    assert body["database"] == "ok"
