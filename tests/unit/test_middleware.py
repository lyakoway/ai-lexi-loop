"""Tests for observability: request id, access log counters, /metrics."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core import metrics
from tests.conftest import make_settings


@pytest.fixture(autouse=True)
def clean_counters() -> None:
    metrics.reset_counters()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app(make_settings())
    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_request_id_is_generated_and_echoed(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


async def test_incoming_request_id_is_respected(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "my-trace-42"})
    assert response.headers.get("X-Request-ID") == "my-trace-42"


async def test_request_counters_increase(client: AsyncClient) -> None:
    metrics.reset_counters()
    await client.get("/health")
    await client.get("/health")
    assert metrics.get_counters()["http_requests_total"] >= 2


async def test_metrics_endpoint_renders_prometheus_text(
    client: AsyncClient,
) -> None:
    await client.get("/health")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert "# TYPE http_requests_total counter" in response.text


async def test_api_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/profile")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


async def test_unhandled_error_counts_as_error(client: AsyncClient) -> None:
    metrics.reset_counters()
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    response = await client.get("/boom")
    assert response.status_code == 500
    assert metrics.get_counters()["http_errors_total"] == 1
