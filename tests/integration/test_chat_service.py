"""Integration tests for ChatService (Stage 6.4.2).

Real PostgreSQL + FakeLlmClient: pair atomicity, fencing (stale claim),
rate limiting, idempotency, auto-close, analytics, and isolation.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import AnalyticsEvent, TutorConversation, TutorMessage
from app.db.session import SessionFactory, session_scope
from app.repositories.chat import (
    TutorConversationRepository,
    TutorMessageRepository,
)
from app.services.tutor import ChatService, ChatUnavailable
from tests.conftest import FakeLlmClient, make_settings
from tests.integration.conftest import (
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

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

VALID_REPLY = (
    '{"reply": "Sounds good! Where are you going?", '
    '"correction": "", "correction_note": "", '
    '"better_alternative": "", "follow_up_question": ""}'
)


def _expire_and_steal_hook(
    session_factory: SessionFactory, conversation_id: int
) -> Any:
    """Hook fired inside the LLM call: expires the current claim and
    captures the dialog with a new one — simulating the takeover."""

    async def hook() -> None:
        stolen = uuid.uuid4()
        async with session_scope(session_factory) as session:
            conversation = await session.get(TutorConversation, conversation_id)
            assert conversation is not None
            conversation.generation_started_at = datetime.now(UTC) - timedelta(
                seconds=1000
            )
        async with session_scope(session_factory) as session:
            claim = await TutorConversationRepository(session).try_claim(
                user_id=conversation.user_id,
                conversation_id=conversation_id,
                claim_id=stolen,
                min_interval_seconds=0,
                claim_timeout_seconds=199,
                now=datetime.now(UTC),
            )
            assert claim == "claimed"

    return hook


def make_service(
    session_factory: SessionFactory,
    llm_client: FakeLlmClient | None,
    *,
    min_interval_seconds: float = 0.0,
) -> ChatService:
    return ChatService(
        session_factory,
        llm_client,
        make_settings(
            llm_chat_min_interval_seconds=min_interval_seconds,
            llm_chat_claim_timeout_seconds=None,
        ),
    )


class HookedFakeLlm(FakeLlmClient):
    """Fake client that fires a hook right before answering."""

    def __init__(self, contents: list[str], hook: Any) -> None:
        super().__init__(contents)
        self._hook = hook

    async def complete_structured(self, request: Any, schema: Any) -> Any:
        from app.integrations.llm.client import parse_structured

        hook, self._hook = self._hook, None
        if hook is not None:
            await hook()
        response = await self.complete(request)
        return parse_structured(response.content, schema)


async def prepare(session_factory: SessionFactory, telegram_id: int) -> int:
    async with session_scope(session_factory) as session:
        profile = await create_profile_row(session, telegram_id=telegram_id)
        return profile.user_id


async def start(service: ChatService, user_id: int) -> int:
    conversation = await service.start_chat(user_id, topic="everyday")
    assert not isinstance(conversation, ChatUnavailable)
    return conversation.id


async def message_count_of(session_factory: SessionFactory, user_id: int) -> int:
    async with session_scope(session_factory) as session:
        conversations = (
            (
                await session.execute(
                    select(TutorConversation).where(
                        TutorConversation.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return sum(conversation.message_count for conversation in conversations)


async def test_start_chat_creates_then_resumes(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9300)
    service = make_service(session_factory, FakeLlmClient([]))

    created = await service.start_chat(user_id, topic="everyday")
    resumed = await service.start_chat(user_id, topic="everyday")

    assert not isinstance(created, ChatUnavailable)
    assert not isinstance(resumed, ChatUnavailable)
    assert resumed.id == created.id  # one active dialog, not a new one


async def test_start_chat_without_profile_fails(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    async with session_scope(session_factory) as session:
        await reset_database(session)

    service = make_service(session_factory, FakeLlmClient([]))
    result = await service.start_chat(999999)

    assert isinstance(result, ChatUnavailable)
    assert result.reason == "profile_not_found"


async def test_send_message_saves_pair_and_bumps_counter(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9301)
    service = make_service(session_factory, FakeLlmClient([VALID_REPLY]))
    conversation_id = await start(service, user_id)

    result = await service.send_message(
        user_id, conversation_id, "I go to store yesterday"
    )

    assert not isinstance(result, ChatUnavailable)
    assert result.reply.reply == "Sounds good! Where are you going?"
    assert result.user_message.role == "user"
    assert result.user_message.text == "I go to store yesterday"
    assert result.assistant_message is not None
    assert result.assistant_message.in_reply_to_message_id == result.user_message.id
    assert result.conversation.message_count == 2
    assert not result.closed


async def test_history_returns_saved_pair(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9302)
    service = make_service(session_factory, FakeLlmClient([VALID_REPLY]))
    conversation_id = await start(service, user_id)
    await service.send_message(user_id, conversation_id, "hello tutor")

    history = await service.get_history(user_id, conversation_id)
    assert [message.role for message in history] == ["user", "assistant"]


async def test_rate_limited_after_recent_attempt(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9303)
    service = make_service(
        session_factory,
        FakeLlmClient([VALID_REPLY, VALID_REPLY]),
        min_interval_seconds=999,
    )
    conversation_id = await start(service, user_id)

    first = await service.send_message(user_id, conversation_id, "hello")
    assert not isinstance(first, ChatUnavailable)
    second = await service.send_message(user_id, conversation_id, "again")
    assert isinstance(second, ChatUnavailable)
    assert second.reason == "rate_limited"


async def test_llm_disabled_degrades_without_messages(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9304)
    service = make_service(session_factory, None)
    conversation_id = await start(service, user_id)

    result = await service.send_message(user_id, conversation_id, "hello")

    assert isinstance(result, ChatUnavailable)
    assert result.reason == "llm_disabled"
    assert await message_count_of(session_factory, user_id) == 0


async def test_llm_error_releases_claim_and_allows_retry(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """An LLM failure leaves no partial pair; a retry is permitted."""
    user_id = await prepare(session_factory, 9305)
    from app.integrations.llm.exceptions import LlmTransportError

    failing = FakeLlmClient(error=LlmTransportError("provider down"))
    service = make_service(session_factory, failing)
    conversation_id = await start(service, user_id)

    failed = await service.send_message(user_id, conversation_id, "hello")
    assert isinstance(failed, ChatUnavailable)
    assert failed.reason == "llm_error"
    assert await message_count_of(session_factory, user_id) == 0

    # Retry with a working provider: the claim was released.
    working = FakeLlmClient([VALID_REPLY])
    working_service = make_service(session_factory, working)
    retried = await working_service.send_message(user_id, conversation_id, "hello")
    assert not isinstance(retried, ChatUnavailable)
    assert await message_count_of(session_factory, user_id) == 2


async def test_invalid_output_degrades_without_pair(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9306)
    service = make_service(session_factory, FakeLlmClient(["definitely not json"]))
    conversation_id = await start(service, user_id)

    result = await service.send_message(user_id, conversation_id, "hello")

    assert isinstance(result, ChatUnavailable)
    assert result.reason == "invalid_output"
    assert await message_count_of(session_factory, user_id) == 0


async def test_idempotent_replay_returns_saved_reply_without_new_llm_call(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9307)
    llm = FakeLlmClient([VALID_REPLY])
    service = make_service(session_factory, llm)
    conversation_id = await start(service, user_id)

    first = await service.send_message(
        user_id,
        conversation_id,
        "I go to store yesterday",
        client_request_id="idem-key-1",
    )
    assert not isinstance(first, ChatUnavailable)

    replay = await service.send_message(
        user_id,
        conversation_id,
        "I go to store yesterday",
        client_request_id="idem-key-1",
    )

    assert not isinstance(replay, ChatUnavailable)
    assert replay.replay is True
    assert replay.reply.reply == first.reply.reply
    assert llm.requests and len(llm.requests) == 1  # no second LLM call


async def test_same_key_different_text_conflicts(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9308)
    llm = FakeLlmClient([VALID_REPLY])
    service = make_service(session_factory, llm)
    conversation_id = await start(service, user_id)
    await service.send_message(
        user_id,
        conversation_id,
        "I go to store yesterday",
        client_request_id="idem-key-2",
    )

    conflict = await service.send_message(
        user_id,
        conversation_id,
        "a completely different message",
        client_request_id="idem-key-2",
    )

    assert isinstance(conflict, ChatUnavailable)
    assert conflict.reason == "idempotency_conflict"


async def test_same_key_during_generation_is_busy(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """A fresh claim (generation in progress) reports busy."""
    user_id = await prepare(session_factory, 9309)
    llm = FakeLlmClient([VALID_REPLY])
    service = make_service(session_factory, llm)
    conversation_id = await start(service, user_id)

    # Simulate a generation in progress: a fresh claim on the dialog.
    async with session_scope(session_factory) as session:
        await TutorConversationRepository(session).try_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=uuid.uuid4(),
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=datetime.now(UTC),
        )

    busy = await service.send_message(
        user_id,
        conversation_id,
        "hello",
        client_request_id="idem-key-busy",
    )

    assert isinstance(busy, ChatUnavailable)
    assert busy.reason == "busy"


async def test_stale_claim_cannot_save_or_release(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Fencing: A → stale → B → A finishes. A saves nothing and cannot
    release B's claim; B's state stays untouched."""
    user_id = await prepare(session_factory, 9310)
    service = make_service(session_factory, FakeLlmClient([VALID_REPLY]))
    conversation_id = await start(service, user_id)

    # A's send claims and is answering (LLM in flight)…
    claim_id_a = uuid.uuid4()
    async with session_scope(session_factory) as session:
        claim = await TutorConversationRepository(session).try_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=claim_id_a,
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=datetime.now(UTC),
        )
        assert claim == "claimed"

    # …but the claim expires (the whole budget elapsed)…
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        conversation.generation_started_at = datetime.now(UTC) - timedelta(seconds=1000)

    # …and B takes over with a new claim.
    claim_id_b = uuid.uuid4()
    async with session_scope(session_factory) as session:
        claim = await TutorConversationRepository(session).try_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=claim_id_b,
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=datetime.now(UTC),
        )
        assert claim == "claimed"

    # A's final save must be rejected by fencing.
    async with session_scope(session_factory) as session:
        saved = await TutorMessageRepository(session).finalize_exchange(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=claim_id_a,
            user_text="stale answer text",
            assistant_text="stale reply",
            client_request_id=None,
            prompt_version="tutor_chat_v1",
            max_messages=100,
        )
        assert saved is None  # nothing saved by the stale request

    # A's release cannot remove B's claim either.
    async with session_scope(session_factory) as session:
        released = await TutorConversationRepository(session).release_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=claim_id_a,
        )
        assert released is False

    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        assert conversation.generation_claim_id == claim_id_b
        assert conversation.message_count == 0

    # B's claim expires, then B's own send re-claims (new UUID) and
    # completes successfully after the takeover.
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        conversation.generation_started_at = datetime.now(UTC) - timedelta(seconds=1000)
    service_b = make_service(session_factory, FakeLlmClient([VALID_REPLY]))
    result = await service_b.send_message(user_id, conversation_id, "hello from B")
    assert not isinstance(result, ChatUnavailable)
    assert result.conversation.message_count == 2


async def test_claim_lost_analytics_recorded_after_rollback(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """The claim-lost event survives the save rollback (best-effort
    separate transaction) — even when analytics in the main session
    would have rolled back with it."""
    user_id = await prepare(session_factory, 9311)
    conversation_id = await start(
        make_service(session_factory, FakeLlmClient([])), user_id
    )

    claim_id_a = uuid.uuid4()
    async with session_scope(session_factory) as session:
        claim = await TutorConversationRepository(session).try_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=claim_id_a,
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=datetime.now(UTC),
        )
        assert claim == "claimed"
    # Expire A's claim, then B takes over.
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        conversation.generation_started_at = datetime.now(UTC) - timedelta(seconds=1000)
    claim_id_b = uuid.uuid4()
    async with session_scope(session_factory) as session:
        claim = await TutorConversationRepository(session).try_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=claim_id_b,
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=datetime.now(UTC),
        )
        assert claim == "claimed"

    # A's own send: expire B's stale claim so the service can claim,
    # then the hook (inside the LLM call) expires A's fresh claim and
    # steals the dialog — A's final save must be fenced out, and the
    # claim-lost event is recorded in its own best-effort session.
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        conversation.generation_started_at = datetime.now(UTC) - timedelta(seconds=1000)
    hook = _expire_and_steal_hook(session_factory, conversation_id)
    service = make_service(session_factory, HookedFakeLlm([VALID_REPLY], hook))
    result = await service.send_message(user_id, conversation_id, "stale attempt")
    assert isinstance(result, ChatUnavailable)
    assert result.reason == "claim_lost"

    async with session_scope(session_factory) as session:
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.user_id == user_id,
                        AnalyticsEvent.event_name == "tutor_chat_claim_lost",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1


async def test_auto_close_at_message_limit(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Reaching the 100-message limit closes the dialog in the same
    transaction that saved the final pair."""
    user_id = await prepare(session_factory, 9312)
    conversation_id = await start(
        make_service(session_factory, FakeLlmClient([VALID_REPLY])), user_id
    )
    # Seed the counter just below the limit: the next pair closes it.
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        conversation.message_count = 98

    result = await make_service(
        session_factory, FakeLlmClient([VALID_REPLY])
    ).send_message(user_id, conversation_id, "final message")

    assert not isinstance(result, ChatUnavailable)
    assert result.closed is True
    assert result.conversation.message_count == 100
    assert result.conversation.status == "closed"

    # A closed dialog refuses further messages.
    late = await make_service(
        session_factory, FakeLlmClient([VALID_REPLY])
    ).send_message(user_id, conversation_id, "after close")
    assert isinstance(late, ChatUnavailable)
    assert late.reason == "conversation_closed"


async def test_foreign_conversation_is_not_found(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    owner_id = await prepare(session_factory, 9313)
    stranger_id = await prepare(session_factory, 9314)
    conversation_id = await start(
        make_service(session_factory, FakeLlmClient([VALID_REPLY])), owner_id
    )

    result = await make_service(
        session_factory, FakeLlmClient([VALID_REPLY])
    ).send_message(stranger_id, conversation_id, "intrude")

    assert isinstance(result, ChatUnavailable)
    assert result.reason == "conversation_not_found"


async def test_message_too_long_rejected_without_llm(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare(session_factory, 9315)
    service = make_service(session_factory, FakeLlmClient([]))
    conversation_id = await start(service, user_id)

    result = await service.send_message(user_id, conversation_id, "x" * 1001)

    assert isinstance(result, ChatUnavailable)
    assert result.reason == "message_too_long"


async def test_analytics_failure_does_not_change_result(
    migrated_db: None,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken analytics write must not turn success into failure."""
    from app.repositories import AnalyticsRepository

    user_id = await prepare(session_factory, 9316)
    service = make_service(session_factory, FakeLlmClient([VALID_REPLY]))
    conversation_id = await start(service, user_id)

    def broken_add(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("analytics unavailable")

    # Break analytics only for the exchange itself.
    monkeypatch.setattr(AnalyticsRepository, "add", broken_add)

    result = await service.send_message(user_id, conversation_id, "hello tutor")

    # The exchange still succeeds despite broken analytics.
    assert not isinstance(result, ChatUnavailable)
    assert result.reply.reply != ""
    assert await message_count_of(session_factory, user_id) == 2


# ---------------------------------------------------------------------------
# Final audit regressions (review of Stage 6.4.2)
# ---------------------------------------------------------------------------


async def test_counter_clamped_at_limit_for_odd_counts(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """A non-even counter (defensive edge) must clamp to exactly 100."""
    user_id = await prepare(session_factory, 9317)
    conversation_id = await start(
        make_service(session_factory, FakeLlmClient([VALID_REPLY])), user_id
    )
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        conversation.message_count = 99

    result = await make_service(
        session_factory, FakeLlmClient([VALID_REPLY])
    ).send_message(user_id, conversation_id, "final pair")

    assert not isinstance(result, ChatUnavailable)
    assert result.closed is True
    assert result.conversation.message_count == 100  # clamped, not 101


async def test_idempotency_pending_when_key_saved_without_reply(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Defensive branch: a saved key without a linked reply reports
    idempotency_pending, never a neighbouring message."""
    user_id = await prepare(session_factory, 9318)
    service = make_service(session_factory, FakeLlmClient([]))
    conversation_id = await start(service, user_id)
    # Simulate a historic orphaned user message (no atomic pair possible
    # in the current flow; the branch guards future changes).
    async with session_scope(session_factory) as session:
        session.add(
            TutorMessage(
                conversation_id=conversation_id,
                role="user",
                text="orphan",
                client_request_id="orphan-key",
            )
        )
        await session.flush()

    result = await service.send_message(
        user_id,
        conversation_id,
        "orphan",
        client_request_id="orphan-key",
    )

    assert isinstance(result, ChatUnavailable)
    assert result.reason == "idempotency_pending"


async def test_retry_after_claim_lost_starts_new_attempt(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """After claim_lost nothing was saved: the same key+text starts a
    fresh attempt (no pending, no conflict)."""
    user_id = await prepare(session_factory, 9319)
    conversation_id = await start(
        make_service(session_factory, FakeLlmClient([VALID_REPLY])), user_id
    )
    hook = _expire_and_steal_hook(session_factory, conversation_id)
    service = make_service(session_factory, HookedFakeLlm([VALID_REPLY], hook))
    lost = await service.send_message(
        user_id,
        conversation_id,
        "try after takeover",
        client_request_id="retry-key",
    )
    assert isinstance(lost, ChatUnavailable)
    assert lost.reason == "claim_lost"

    # B's claim expires; the retry re-claims and succeeds.
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, conversation_id)
        assert conversation is not None
        conversation.generation_started_at = datetime.now(UTC) - timedelta(seconds=1000)
    retry = await make_service(
        session_factory, FakeLlmClient([VALID_REPLY])
    ).send_message(
        user_id,
        conversation_id,
        "try after takeover",
        client_request_id="retry-key",
    )
    assert not isinstance(retry, ChatUnavailable)
    assert retry.user_message.client_request_id == "retry-key"


async def test_rate_limit_covers_failed_llm_attempts(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Failed LLM attempts are rate limited too (last_generation_at is
    set at claim time, before the LLM call)."""
    from app.integrations.llm.exceptions import LlmTransportError

    user_id = await prepare(session_factory, 9320)
    service = make_service(
        session_factory,
        FakeLlmClient(error=LlmTransportError("down")),
        min_interval_seconds=999,
    )
    conversation_id = await start(service, user_id)

    failed = await service.send_message(user_id, conversation_id, "hello")
    assert isinstance(failed, ChatUnavailable)
    assert failed.reason == "llm_error"

    again = await service.send_message(user_id, conversation_id, "hello")
    assert isinstance(again, ChatUnavailable)
    assert again.reason == "rate_limited"  # failed attempt counted
