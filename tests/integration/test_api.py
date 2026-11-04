"""End-to-end HTTP API tests on a real PostgreSQL.

Drives the public /api/v1 endpoints through a real ASGI app with the
real database: authentication, ownership isolation, validation, status
codes and analytics sources.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.app import create_app
from app.db.models import AnalyticsEvent, Profile
from app.db.session import SessionFactory, session_scope
from app.repositories import ApiTokenRepository
from app.services.lessons import LessonPlan
from app.services.tokens import ApiTokenService
from tests.conftest import make_settings
from tests.integration.conftest import (
    TEST_DATABASE_URL,
    create_profile_row,
    create_user,
    insert_test_expression,
    make_lesson_service,
    reset_database,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def client(migrated_db: None) -> Any:
    app = create_app(make_settings(database_url=TEST_DATABASE_URL))
    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def issue_token(factory: SessionFactory, user_id: int) -> str:
    """Issue a personal API token for a user."""
    async with session_scope(factory) as session:
        issued = await ApiTokenService(ApiTokenRepository(session)).issue(user_id)
    return issued.token


@pytest_asyncio.fixture
async def alice(migrated_db: None, session_factory: SessionFactory) -> Any:
    """A configured user with two fresh expressions and an API token."""
    async with session_scope(session_factory) as session:
        await reset_database(session)
        await insert_test_expression(session, text_value="alice-1")
        await insert_test_expression(session, text_value="alice-2")
        profile = await create_profile_row(session, telegram_id=6001)
        issued = await ApiTokenService(ApiTokenRepository(session)).issue(
            profile.user_id
        )
    return {"user_id": profile.user_id, "token": issued.token}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_missing_token_is_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/v1/profile")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


async def test_invalid_token_is_unauthorized(
    client: AsyncClient, migrated_db: None
) -> None:
    response = await client.get("/api/v1/profile", headers=auth("alex_totally-wrong"))
    assert response.status_code == 401


async def test_get_profile_returns_stored_settings(
    client: AsyncClient, alice: Any
) -> None:
    response = await client.get("/api/v1/profile", headers=auth(alice["token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["target_language"] == "ts"
    assert body["level"] == "A1"
    assert body["new_items_per_day"] == 5
    assert "user_id" not in body


async def test_get_profile_without_profile_is_404(
    client: AsyncClient, migrated_db: None, session_factory: SessionFactory
) -> None:
    async with session_scope(session_factory) as session:
        user = await create_user(session, telegram_id=6100)
    token = await issue_token(session_factory, user.id)

    response = await client.get("/api/v1/profile", headers=auth(token))

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found"


async def test_patch_profile_updates_single_field(
    client: AsyncClient, alice: Any, session_factory: SessionFactory
) -> None:
    response = await client.patch(
        "/api/v1/profile",
        headers=auth(alice["token"]),
        json={"level": "B2"},
    )
    assert response.status_code == 200
    assert response.json()["level"] == "B2"

    async with session_scope(session_factory) as session:
        stored = await session.execute(
            select(Profile).where(Profile.user_id == alice["user_id"])
        )
        profile = stored.scalar_one()
        assert profile.level == "B2"
        assert profile.new_items_per_day == 5  # untouched


async def test_patch_profile_validates_values(client: AsyncClient, alice: Any) -> None:
    response = await client.patch(
        "/api/v1/profile",
        headers=auth(alice["token"]),
        json={"level": "Z9"},
    )
    assert response.status_code == 422


async def test_post_lesson_returns_cards(
    client: AsyncClient,
    alice: Any,
    session_factory: SessionFactory,
) -> None:
    response = await client.post("/api/v1/lessons", headers=auth(alice["token"]))
    assert response.status_code == 201
    body = response.json()
    assert body["lesson_ref"]
    assert len(body["expressions"]) == 2
    card = body["expressions"][0]
    assert {
        "expression_id",
        "text",
        "translation",
        "ipa",
        "phonetic_hint_ru",
        "example",
        "example_translation",
    } <= set(card)

    async with session_scope(session_factory) as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.user_id == alice["user_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    names = [(event.event_name, event.source) for event in events]
    assert ("lesson_started", "web") in names
    assert names.count(("expression_shown", "web")) == 2

    # A second build finds nothing new.
    response = await client.post("/api/v1/lessons", headers=auth(alice["token"]))
    assert response.status_code == 201
    assert response.json()["unavailable_reason"] == "all_served"


async def test_reviews_due_and_answer_flow(
    client: AsyncClient,
    alice: Any,
    session_factory: SessionFactory,
) -> None:
    # Serve a lesson ten days ago, so its card is already due.
    async with session_scope(session_factory) as session:
        profile_row = (
            await session.execute(
                select(Profile).where(Profile.user_id == alice["user_id"])
            )
        ).scalar_one()
        plan = await make_lesson_service(session).build_lesson(
            alice["user_id"], profile_row, now=NOW - timedelta(days=10)
        )
        assert isinstance(plan, LessonPlan)
        expression_id = plan.expressions[0].id

    due = await client.get("/api/v1/reviews/due", headers=auth(alice["token"]))
    assert due.status_code == 200
    assert any(card["expression_id"] == expression_id for card in due.json()["cards"])

    answer = await client.post(
        f"/api/v1/reviews/{expression_id}/answer",
        headers=auth(alice["token"]),
        json={"remembered": True},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["review_count"] == 1
    assert body["interval_days"] == 1

    # After a successful answer the card is scheduled for tomorrow and
    # leaves the due list (its sibling card may still be due).
    after = await client.get("/api/v1/reviews/due", headers=auth(alice["token"]))
    assert after.status_code == 200
    assert all(card["expression_id"] != expression_id for card in after.json()["cards"])


async def test_answer_for_foreign_expression_is_404(
    client: AsyncClient, alice: Any
) -> None:
    response = await client.post(
        "/api/v1/reviews/999999/answer",
        headers=auth(alice["token"]),
        json={"remembered": True},
    )
    assert response.status_code == 404


async def test_user_cannot_read_foreign_profile(
    client: AsyncClient,
    alice: Any,
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """Another user's token sees only their own (empty) data."""
    async with session_scope(session_factory) as session:
        other = await create_user(session, telegram_id=6200)
    other_token = await issue_token(session_factory, other.id)

    profile_response = await client.get("/api/v1/profile", headers=auth(other_token))
    assert profile_response.status_code == 404

    stats_response = await client.get(
        "/api/v1/stats/overview", headers=auth(other_token)
    )
    assert stats_response.status_code == 200
    assert stats_response.json()["expressions_shown"] == 0


async def test_stats_overview_via_api(client: AsyncClient, alice: Any) -> None:
    await client.post("/api/v1/lessons", headers=auth(alice["token"]))

    response = await client.get("/api/v1/stats/overview", headers=auth(alice["token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["expressions_shown"] == 2
    assert body["due_count"] == 0


async def test_stats_progress_via_api(client: AsyncClient, alice: Any) -> None:
    await client.post("/api/v1/lessons", headers=auth(alice["token"]))

    response = await client.get("/api/v1/stats/progress", headers=auth(alice["token"]))
    assert response.status_code == 200
    assert len(response.json()["days"]) == 1


async def test_openapi_documents_api(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/profile" in paths
    assert "/api/v1/lessons" in paths
    assert "/api/v1/reviews/{expression_id}/answer" in paths
