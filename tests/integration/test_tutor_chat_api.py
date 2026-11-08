"""Integration tests for the AI Tutor Chat API (Stage 6.4.3).

Real PostgreSQL + FakeLlmClient: authentication, create/resume/list,
history, send with Idempotency-Key, close/delete, ownership on every
endpoint, HTTP error mapping, and OpenAPI registration.
"""

import os
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.app import create_app
from app.db.models import TutorConversation, TutorMessage
from app.db.session import SessionFactory, session_scope
from app.repositories import ApiTokenRepository
from app.services.tokens import ApiTokenService
from tests.conftest import FakeLlmClient, make_settings
from tests.integration.conftest import (
    TEST_DATABASE_URL,
    create_profile_row,
    reset_database,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

VALID_REPLY = (
    '{"reply": "Sounds great!", "correction": "", "correction_note": "", '
    '"better_alternative": "", "follow_up_question": ""}'
)


class Clients:
    """Two authenticated users over one app."""

    def __init__(
        self, alice: AsyncClient, bob: AsyncClient, alice_user_id: int
    ) -> None:
        self.alice = alice
        self.bob = bob
        self.alice_user_id = alice_user_id


@pytest_asyncio.fixture
async def api(migrated_db: None, session_factory: SessionFactory) -> Any:
    """App + two users (alice configured, bob fresh) + fake LLM."""
    app = create_app(make_settings(database_url=TEST_DATABASE_URL))
    llm = FakeLlmClient([VALID_REPLY])
    async with LifespanManager(app):
        app.state.llm_client = llm
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test"):
            async with session_scope(session_factory) as session:
                await reset_database(session)
                profile = await create_profile_row(session, telegram_id=9400)
                issued = await ApiTokenService(ApiTokenRepository(session)).issue(
                    profile.user_id
                )
                alice_token = issued.token
                alice_user_id = profile.user_id

            async with session_scope(session_factory) as session:
                stranger = await create_profile_row(session, telegram_id=9401)
                stranger_token = await ApiTokenService(
                    ApiTokenRepository(session)
                ).issue(stranger.user_id)

            clients = Clients(
                AsyncClient(
                    transport=transport,
                    base_url="http://test",
                    headers={"Authorization": f"Bearer {alice_token}"},
                ),
                AsyncClient(
                    transport=transport,
                    base_url="http://test",
                    headers={"Authorization": f"Bearer {stranger_token.token}"},
                ),
                alice_user_id,
            )
            # Expose transport-scoped clients with app-level LLM intact.
            clients.alice._transport = transport
            clients.bob._transport = transport
            clients.llm = llm  # type: ignore[attr-defined]
            yield clients


async def test_chat_endpoints_require_auth(session_factory: Any) -> None:
    app = create_app(make_settings(database_url=TEST_DATABASE_URL))
    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            for method, path, body in (
                ("post", "/api/v1/tutor/chat", None),
                ("get", "/api/v1/tutor/chat", None),
                ("get", "/api/v1/tutor/chat/active", None),
                ("get", "/api/v1/tutor/chat/1/messages", None),
                ("post", "/api/v1/tutor/chat/1/messages", {"text": "hi"}),
                ("post", "/api/v1/tutor/chat/1/close", None),
                ("delete", "/api/v1/tutor/chat/1", None),
            ):
                if method == "get":
                    response = await http.get(path)
                else:
                    response = await http.request(method, path, json=body)
                assert response.status_code == 401, path


async def test_start_creates_then_returns_same_active(
    api: Any, session_factory: SessionFactory
) -> None:
    first = await api.alice.post("/api/v1/tutor/chat", json={"topic": "everyday"})
    assert first.status_code == 200
    conversation_id = first.json()["id"]
    assert first.json()["status"] == "active"
    assert first.json()["level"] == "A1"

    second = await api.alice.post("/api/v1/tutor/chat", json={})
    assert second.status_code == 200
    assert second.json()["id"] == conversation_id  # same active dialog


async def test_get_active_returns_404_when_absent(
    api: Any, session_factory: SessionFactory
) -> None:
    response = await api.alice.get("/api/v1/tutor/chat/active")
    assert response.status_code == 404


async def test_get_active_returns_dialog(
    api: Any, session_factory: SessionFactory
) -> None:
    started = await api.alice.post("/api/v1/tutor/chat", json={})
    conversation_id = started.json()["id"]

    response = await api.alice.get("/api/v1/tutor/chat/active")
    assert response.status_code == 200
    assert response.json()["id"] == conversation_id


async def test_list_returns_only_own_dialogs(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})

    own = await api.alice.get("/api/v1/tutor/chat")
    assert own.status_code == 200
    assert len(own.json()) == 1

    stranger_list = await api.bob.get("/api/v1/tutor/chat")
    assert stranger_list.status_code == 200
    assert stranger_list.json() == []


async def test_send_message_saves_pair_with_link(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})
    response = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hello tutor"},
        headers={"Idempotency-Key": "send-key-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "Idempotent-Replay" not in response.headers
    user_message = body["user_message"]
    assistant = body["assistant_message"]
    assert user_message["role"] == "user"
    assert assistant["in_reply_to_message_id"] == user_message["id"]
    assert body["reply"]["reply"] == "Sounds great!"
    assert body["conversation_status"] == "active"

    # Internal fields are not exposed.
    assert "prompt_version" not in assistant
    assert "error_kind" not in assistant
    assert "generation_claim_id" not in body["reply"]


async def test_replay_returns_saved_reply_without_new_llm_call(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})
    first = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hello tutor"},
        headers={"Idempotency-Key": "replay-key-1"},
    )
    assert first.status_code == 200
    calls_after_first = len(api.llm.requests)

    replay = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hello tutor"},
        headers={"Idempotency-Key": "replay-key-1"},
    )

    assert replay.status_code == 200
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert replay.json()["reply"]["reply"] == first.json()["reply"]["reply"]
    assert len(api.llm.requests) == calls_after_first  # no new LLM call


async def test_same_key_different_text_conflicts(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})
    await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "first message"},
        headers={"Idempotency-Key": "conflict-key"},
    )
    conflict = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "a different message"},
        headers={"Idempotency-Key": "conflict-key"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency_conflict"


async def test_missing_or_malformed_key_is_422(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})

    missing = await api.alice.post("/api/v1/tutor/chat/1/messages", json={"text": "hi"})
    assert missing.status_code == 422

    short = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hi"},
        headers={"Idempotency-Key": "abc"},
    )
    assert short.status_code == 422

    bad_chars = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hi"},
        headers={"Idempotency-Key": "has spaces!!"},
    )
    assert bad_chars.status_code == 422


async def test_too_long_text_is_422(api: Any) -> None:
    response = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "x" * 1001},
        headers={"Idempotency-Key": "long-text-key"},
    )
    assert response.status_code == 422


async def test_busy_reports_409(api: Any, session_factory: SessionFactory) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})
    # A fresh claim (simulated generation in progress).
    async with session_scope(session_factory) as session:
        import uuid as uuid_module

        from app.repositories.chat import TutorConversationRepository

        await TutorConversationRepository(session).try_claim(
            user_id=api.alice_user_id,
            conversation_id=1,
            claim_id=uuid_module.uuid4(),
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=datetime.now(UTC),
        )
    busy = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hi"},
        headers={"Idempotency-Key": "busy-key-01"},
    )
    assert busy.status_code == 409
    assert busy.json()["detail"] == "busy"


async def test_rate_limited_reports_429(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})
    # Rate limit was just consumed by the start flow's message? No —
    # simulate a recent attempt: claim then release (last_generation_at set).
    async with session_scope(session_factory) as session:
        import uuid as uuid_module

        from app.repositories.chat import TutorConversationRepository

        await TutorConversationRepository(session).try_claim(
            user_id=api.alice_user_id,
            conversation_id=1,
            claim_id=uuid_module.uuid4(),
            min_interval_seconds=999,
            claim_timeout_seconds=199,
            now=datetime.now(UTC),
        )
        # Release the claim, but last_generation_at stays recent.
        await TutorConversationRepository(session).release_claim(
            user_id=api.alice_user_id,
            conversation_id=1,
            claim_id=await session.scalar(
                select(TutorConversation.generation_claim_id).where(
                    TutorConversation.id == 1
                )
            )
            or uuid_module.uuid4(),
        )
    rate = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hi"},
        headers={"Idempotency-Key": "rate-key-01"},
    )
    assert rate.status_code == 429
    assert rate.json()["detail"] == "rate_limited"


async def test_llm_unavailable_reports_503(api: Any) -> None:
    api.llm_disabled = True
    api.alice._transport.app.state.llm_client = None

    await api.alice.post("/api/v1/tutor/chat", json={})
    response = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hi"},
        headers={"Idempotency-Key": "disabled-key"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "llm_disabled"


async def test_closed_dialog_reports_409(api: Any) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})
    closed = await api.alice.post("/api/v1/tutor/chat/1/close")
    assert closed.status_code == 200

    late = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hi"},
        headers={"Idempotency-Key": "after-close-key"},
    )
    assert late.status_code == 409
    assert late.json()["detail"] == "conversation_closed"

    re_close = await api.alice.post("/api/v1/tutor/chat/1/close")
    assert re_close.status_code == 409


async def test_foreign_conversation_is_404_without_llm(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.bob.post("/api/v1/tutor/chat", json={})
    calls_before = len(api.llm.requests)

    history = await api.alice.get("/api/v1/tutor/chat/1/messages")
    assert history.status_code == 404

    send = await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "intrude"},
        headers={"Idempotency-Key": "intrude-key-1"},
    )
    assert send.status_code == 404

    close = await api.alice.post("/api/v1/tutor/chat/1/close")
    assert close.status_code == 404

    delete = await api.alice.delete("/api/v1/tutor/chat/1")
    assert delete.status_code == 404

    # No LLM call was made for the foreign dialog.
    assert len(api.llm.requests) == calls_before


async def test_delete_removes_dialog_and_messages(
    api: Any, session_factory: SessionFactory
) -> None:
    await api.alice.post("/api/v1/tutor/chat", json={})
    await api.alice.post(
        "/api/v1/tutor/chat/1/messages",
        json={"text": "hello"},
        headers={"Idempotency-Key": "delete-key-1"},
    )

    deleted = await api.alice.delete("/api/v1/tutor/chat/1")
    assert deleted.status_code == 200

    async with session_scope(session_factory) as session:
        remaining_messages = (
            (
                await session.execute(
                    select(TutorMessage).where(TutorMessage.conversation_id == 1)
                )
            )
            .scalars()
            .all()
        )
    assert remaining_messages == []  # CASCADE

    history = await api.alice.get("/api/v1/tutor/chat/1/messages")
    assert history.status_code == 404


async def test_openapi_registers_chat_paths_and_key_header(
    api: Any,
) -> None:
    schema = (await api.alice.get("/openapi.json")).json()
    paths = schema["paths"]
    for path in (
        "/api/v1/tutor/chat",
        "/api/v1/tutor/chat/active",
        "/api/v1/tutor/chat/{conversation_id}/messages",
        "/api/v1/tutor/chat/{conversation_id}/close",
        "/api/v1/tutor/chat/{conversation_id}",
    ):
        assert path in paths, path

    send_op = paths["/api/v1/tutor/chat/{conversation_id}/messages"]["post"]
    key_param = next(
        param for param in send_op["parameters"] if param["name"] == "Idempotency-Key"
    )
    assert key_param["required"] is True
    assert "bearerAuth" in str(schema.get("components", {})) or schema.get(
        "components", {}
    ).get("securitySchemes")
