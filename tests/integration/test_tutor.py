"""Integration tests for the Explain Expression scenario (Stage 6.2).

Drives the HTTP endpoint on a real PostgreSQL with a fake LLM client
injected into app.state: analytics events, ownership isolation and —
critically — proof that explanations never touch SRS progress.
"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.app import create_app
from app.db.models import AnalyticsEvent, ExpressionProgress
from app.db.session import SessionFactory, session_scope
from app.repositories import ApiTokenRepository
from app.services import TutorService
from app.services.lessons import LessonPlan
from app.services.tokens import ApiTokenService
from tests.conftest import FakeLlmClient, make_settings
from tests.integration.conftest import (
    TEST_DATABASE_URL,
    create_profile_row,
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

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

VALID_ANSWER = (
    '{"meaning": "Ни пуха ни пера", '
    '"explanation": "Идиоматическое пожелание удачи.", '
    '"usage_notes": [], "common_mistakes": [], '
    '"examples": [{"text": "Break a leg!", "translation": "Удачи!"}], '
    '"mini_dialogue": null}'
)


class Alice:
    """Configured user with a served expression and an API token."""

    def __init__(self, user_id: int, token: str, expression_id: int) -> None:
        self.user_id = user_id
        self.token = token
        self.expression_id = expression_id


async def prepare_alice(
    session_factory: SessionFactory,
) -> Alice:
    async with session_scope(session_factory) as session:
        await reset_database(session)
        await insert_test_expression(session, text_value="alice-card")
        profile = await create_profile_row(session, telegram_id=6001)
        lesson = make_lesson_service(session)
        plan = await lesson.build_lesson(profile.user_id, profile, now=NOW)
        assert isinstance(plan, LessonPlan)
        issued = await ApiTokenService(ApiTokenRepository(session)).issue(
            profile.user_id
        )
        return Alice(profile.user_id, issued.token, plan.expressions[0].id)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(
    migrated_db: None, session_factory: SessionFactory
) -> AsyncIterator[tuple[AsyncClient, Alice, FakeLlmClient]]:
    app = create_app(make_settings(database_url=TEST_DATABASE_URL))
    fake_llm = FakeLlmClient([VALID_ANSWER])
    async with LifespanManager(app):
        # Simulate an enabled LLM integration without any network.
        app.state.llm_client = fake_llm
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            alice = await prepare_alice(session_factory)
            yield http, alice, fake_llm


async def test_explain_via_api_returns_explanation(
    client: tuple[AsyncClient, Alice, FakeLlmClient],
    session_factory: SessionFactory,
) -> None:
    http, alice, fake_llm = client

    response = await http.post(
        f"/api/v1/expressions/{alice.expression_id}/explain",
        headers=auth(alice.token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meaning"]
    assert body["examples"][0]["translation"]
    request = fake_llm.requests[0]
    assert request.prompt_version == "explain_v1"
    assert request.messages[0].role == "system"

    # The analytics event carries the web source and success flag.
    async with session_scope(session_factory) as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.user_id == alice.user_id,
                        AnalyticsEvent.event_name == "tutor_explained",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    event = events[0]
    assert event.source == "web"
    assert event.properties["success"] is True
    assert event.properties["prompt_version"] == "explain_v1"


async def test_explain_does_not_touch_srs_progress(
    client: tuple[AsyncClient, Alice, FakeLlmClient],
    session_factory: SessionFactory,
) -> None:
    http, alice, _ = client

    async with session_scope(session_factory) as session:
        before = (
            (
                await session.execute(
                    select(ExpressionProgress).where(
                        ExpressionProgress.user_id == alice.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        snapshot = [
            (row.expression_id, row.review_count, row.completed_at, row.next_review_at)
            for row in before
        ]
        assert snapshot

    response = await http.post(
        f"/api/v1/expressions/{alice.expression_id}/explain",
        headers=auth(alice.token),
    )
    assert response.status_code == 200

    async with session_scope(session_factory) as session:
        after = (
            (
                await session.execute(
                    select(ExpressionProgress).where(
                        ExpressionProgress.user_id == alice.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [
        (row.expression_id, row.review_count, row.completed_at, row.next_review_at)
        for row in after
    ] == snapshot


async def test_explain_untouched_by_failed_llm(
    client: tuple[AsyncClient, Alice, FakeLlmClient],
    session_factory: SessionFactory,
) -> None:
    """An LLM outage degrades to 503 and leaves all data intact."""
    from app.integrations.llm.exceptions import LlmTransportError

    http, alice, fake_llm = client
    # The realistic provider failure: transport errors reach the service
    # as LlmError subclasses after the retry policy is exhausted.
    fake_llm._error = LlmTransportError("provider unreachable")

    response = await http.post(
        f"/api/v1/expressions/{alice.expression_id}/explain",
        headers=auth(alice.token),
    )
    assert response.status_code == 503

    async with session_scope(session_factory) as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.user_id == alice.user_id,
                        AnalyticsEvent.event_name == "tutor_explained",
                    )
                )
            )
            .scalars()
            .all()
        )
        progress = (
            (
                await session.execute(
                    select(ExpressionProgress).where(
                        ExpressionProgress.user_id == alice.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert events[0].properties["success"] is False
    assert events[0].properties["error"] == "llm_error"
    assert len(progress) == 1  # progress untouched
    assert progress[0].completed_at is None


async def test_explain_foreign_expression_is_404(
    client: tuple[AsyncClient, Alice, FakeLlmClient],
    session_factory: SessionFactory,
) -> None:
    """Another user's (or unknown) expression is invisible: plain 404."""
    http, alice, _ = client
    async with session_scope(session_factory) as session:
        await insert_test_expression(session, text_value="not-alice-card")

    response = await http.post(
        "/api/v1/expressions/999999/explain",
        headers=auth(alice.token),
    )
    assert response.status_code == 404

    # The expression exists but was never served to Alice: same 404,
    # no information leak about its existence.
    async with session_scope(session_factory) as session:
        from app.db.models import Expression

        foreign = (
            await session.execute(
                select(Expression).where(Expression.text == "not-alice-card")
            )
        ).scalar_one()
    response = await http.post(
        f"/api/v1/expressions/{foreign.id}/explain",
        headers=auth(alice.token),
    )
    assert response.status_code == 404


async def test_explain_without_llm_is_503(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """With the integration disabled the endpoint degrades honestly."""
    app = create_app(make_settings(database_url=TEST_DATABASE_URL))
    async with LifespanManager(app):
        assert app.state.llm_client is None
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            alice = await prepare_alice(session_factory)
            response = await http.post(
                f"/api/v1/expressions/{alice.expression_id}/explain",
                headers=auth(alice.token),
            )
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


async def test_explain_requires_auth(migrated_db: None) -> None:
    app = create_app(make_settings(database_url=TEST_DATABASE_URL))
    async with LifespanManager(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post("/api/v1/expressions/1/explain")
    assert response.status_code == 401


async def test_middleware_di_chain_reaches_tutor_service(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """The real DI middleware must produce a working TutorService.

    Exercises the actual chain: DbSessionMiddleware over the real
    session factory builds the service with real repositories, the
    injected (fake) LLM client and the shared analytics — and an
    explanation flows through it into the database.
    """
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock, MagicMock

    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import CallbackQuery, Message, TelegramObject

    from app.bot.handlers import lesson as lesson_handlers
    from app.bot.handlers import tutor as tutor_handlers
    from app.bot.keyboards.tutor import TutorCB
    from app.bot.middlewares.di import DbSessionMiddleware
    from app.db.models import AnalyticsEvent

    # Prepare the user, the card and the lesson flow state.
    async with session_scope(session_factory) as session:
        await reset_database(session)
        await insert_test_expression(session, text_value="di-chain")
        profile = await create_profile_row(session, telegram_id=6300)
        plan = await make_lesson_service(session).build_lesson(
            profile.user_id, profile, now=NOW
        )
        assert isinstance(plan, LessonPlan)
        token = plan.expressions[0].id

    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=1, user_id=6300),
    )
    await state.set_state(lesson_handlers.LessonFlow.active)
    await state.update_data(
        user_id=profile.user_id,
        expression_ids=[expression.id for expression in plan.expressions],
        index=0,
        token=token,
    )

    fake_llm = FakeLlmClient([VALID_ANSWER])
    middleware = DbSessionMiddleware(session_factory, fake_llm)

    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=6300)
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback_data = TutorCB(action="explain", token=token, index=0)

    # The middleware owns the transaction: the real handler must run
    # inside it, exactly like aiogram runs handlers between middleware
    # and the session commit.
    async def inner(event: Any, data: dict[str, Any]) -> None:
        assert data["llm_client"] is fake_llm
        assert isinstance(data["tutor_service"], TutorService)
        await tutor_handlers.on_explain(
            callback,
            callback_data,
            state,
            data["tutor_service"],
            data["lesson_service"],
        )

    # Update is never inspected by this middleware; cast keeps mypy
    # happy about the TelegramObject envelope.
    update = cast(TelegramObject, SimpleNamespace())
    await middleware(inner, update, {"state": state})

    edited = callback.message.edit_text.await_args.args[0]
    assert "di-chain" in edited  # the card text came from the real DB
    assert "Идиоматическое" in edited  # the fake LLM answer was parsed

    # The event was written through the middleware-owned transaction.
    async with session_scope(session_factory) as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.user_id == profile.user_id,
                        AnalyticsEvent.event_name == "tutor_explained",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].source == "telegram"
