"""Unit tests for the /chat Telegram flow (Stage 6.4.4).

Handlers are called directly with a mocked ChatService — no real LLM,
no database. The deterministic idempotency key comes from the injected
Telegram ``update_id``.
"""

import html
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from app.api.deps import _IDEMPOTENCY_KEY_PATTERN, get_idempotency_key
from app.bot import texts
from app.bot.handlers import chat as chat_handlers
from app.bot.handlers.chat import ChatFlow
from app.core.config import Settings
from app.db.models import TutorConversation, TutorMessage
from app.services import ChatService
from app.services.tutor import ChatUnavailable
from app.services.tutor.chat import ChatExchange
from app.services.tutor.chat_outputs import ChatReplyOut

TEST_TELEGRAM_ID = 12345
SETTINGS = Settings(_env_file=None, database_url="postgresql://u:p@h/db")


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=TEST_TELEGRAM_ID)
    return FSMContext(storage=storage, key=key)


async def activate(state: FSMContext) -> None:
    await state.set_state(ChatFlow.active)


def make_message(text: str | None = "hello") -> Any:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.text = text
    message.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    return message


def make_data(update_id: int = 777) -> dict[str, Any]:
    return {"tg_update_id": update_id}


def make_service(result: Any = None) -> Any:
    service = AsyncMock(spec=ChatService)
    service.send_message.return_value = result
    service.get_active_chat.return_value = SimpleNamespace(id=5)
    return service


def make_exchange(
    *,
    closed: bool = False,
    reply_text: str = "Sounds good!",
) -> ChatExchange:
    reply_out = ChatReplyOut(
        reply=reply_text,
        correction="I went to the store yesterday",
        correction_note="Прошедшее время: went, не go",
        better_alternative="I popped over to the store",
        follow_up_question="What did you buy?",
    )
    return ChatExchange(
        conversation=TutorConversation(
            id=5,
            user_id=11,
            language="en",
            native_language="ru",
            level="B1",
            message_count=2,
            status="closed" if closed else "active",
        ),
        user_message=TutorMessage(
            id=1,
            conversation_id=5,
            role="user",
            text="I go to store yesterday",
        ),
        assistant_message=TutorMessage(
            id=2,
            conversation_id=5,
            role="assistant",
            text=reply_text,
        ),
        reply=reply_out,
        closed=closed,
    )


def make_conversation(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "id": 5,
        "user_id": TEST_TELEGRAM_ID,
        "language": "en",
        "native_language": "ru",
        "level": "B1",
        "topic": "everyday",
        "situation": None,
        "status": "active",
        "message_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# /chat
# ---------------------------------------------------------------------------


async def test_cmd_chat_starts_dialog_and_sets_state() -> None:
    state = make_state()
    message = make_message("/chat")
    service = AsyncMock(spec=ChatService)
    service.start_chat.return_value = make_conversation(message_count=0)

    await chat_handlers.cmd_chat(message, state, service, make_data())

    service.start_chat.assert_awaited_once_with(TEST_TELEGRAM_ID, source="telegram")
    greeting = message.answer.await_args.args[0]
    assert "everyday" in greeting
    assert "/close" in greeting
    assert await state.get_state() == ChatFlow.active.state


async def test_cmd_chat_resumes_existing_dialog() -> None:
    state = make_state()
    message = make_message("/chat")
    service = AsyncMock(spec=ChatService)
    service.start_chat.return_value = make_conversation(message_count=4, topic="work")

    await chat_handlers.cmd_chat(message, state, service, make_data())

    greeting = message.answer.await_args.args[0]
    assert "4" in greeting
    assert await state.get_state() == ChatFlow.active.state


async def test_cmd_chat_unavailable_shows_friendly_text() -> None:
    state = make_state()
    message = make_message("/chat")
    service = AsyncMock(spec=ChatService)
    service.start_chat.return_value = ChatUnavailable(reason="llm_disabled")

    await chat_handlers.cmd_chat(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_UNAVAILABLE
    assert await state.get_state() is None


# ---------------------------------------------------------------------------
# Text message inside an open chat
# ---------------------------------------------------------------------------


async def test_message_sends_with_deterministic_key_and_renders_reply() -> None:
    state = make_state()
    await activate(state)
    service = make_service(make_exchange())
    message = make_message("hello tutor")

    await chat_handlers.on_chat_message(
        message, state, service, make_data(update_id=777)
    )

    service.send_message.assert_awaited_once_with(
        TEST_TELEGRAM_ID,
        5,
        "hello tutor",
        client_request_id="tg-update-777",  # deterministic key from update_id
        source="telegram",
    )
    rendered = message.answer.await_args.args[0]
    assert "Sounds good!" in rendered
    assert "What did you buy?" in rendered
    assert await state.get_state() == ChatFlow.active.state


async def test_reply_sections_rendered_and_escaped() -> None:
    state = make_state()
    await activate(state)
    service = make_service(make_exchange(reply_text="Nice! <b>bold</b> & things"))
    message = make_message()

    await chat_handlers.on_chat_message(
        message, state, service, make_data(update_id=778)
    )

    rendered = message.answer.await_args.args[0]
    assert html.escape("Nice! <b>bold</b> & things") in rendered
    assert "Как точнее" in rendered
    assert "Естественнее" in rendered


async def test_busy_maps_to_friendly_text() -> None:
    state = make_state()
    await activate(state)
    service = make_service(ChatUnavailable(reason="busy"))

    message = make_message()

    await chat_handlers.on_chat_message(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_BUSY
    assert "busy" not in message.answer.await_args.args[0]


async def test_rate_limited_maps_to_friendly_text() -> None:
    state = make_state()
    await activate(state)
    service = make_service(ChatUnavailable(reason="rate_limited"))

    message = make_message()

    await chat_handlers.on_chat_message(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_RATE_LIMITED


async def test_llm_error_maps_to_unavailable_text() -> None:
    state = make_state()
    await activate(state)
    service = make_service(ChatUnavailable(reason="llm_error"))

    message = make_message()

    await chat_handlers.on_chat_message(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_UNAVAILABLE


async def test_claim_lost_asks_to_retry_and_keeps_state() -> None:
    state = make_state()
    await activate(state)
    service = make_service(ChatUnavailable(reason="claim_lost"))

    message = make_message()

    await chat_handlers.on_chat_message(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_CLAIM_LOST
    assert await state.get_state() == ChatFlow.active.state


async def test_too_long_maps_to_friendly_text() -> None:
    state = make_state()
    await activate(state)
    service = make_service(ChatUnavailable(reason="message_too_long"))

    message = make_message()

    await chat_handlers.on_chat_message(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_MESSAGE_TOO_LONG


async def test_conversation_closed_clears_state() -> None:
    state = make_state()
    await activate(state)
    service = make_service(ChatUnavailable(reason="conversation_closed"))

    message = make_message()

    await chat_handlers.on_chat_message(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_CLOSED
    assert await state.get_state() is None


async def test_foreign_conversation_neutral_text_and_cleared_state() -> None:
    """Foreign/missing dialog: neutral text, no internals, state cleared."""
    state = make_state()
    await activate(state)
    service = make_service(ChatUnavailable(reason="conversation_not_found"))

    message = make_message()

    await chat_handlers.on_chat_message(message, state, service, make_data())

    assert message.answer.await_args.args[0] == texts.CHAT_NOT_FOUND
    assert "conversation_not_found" not in message.answer.await_args.args[0]
    assert await state.get_state() is None


# ---------------------------------------------------------------------------
# /close, /history, non-text
# ---------------------------------------------------------------------------


async def test_close_closes_dialog_and_clears_state() -> None:
    state = make_state()
    await activate(state)
    service = AsyncMock(spec=ChatService)
    service.get_active_chat.return_value = SimpleNamespace(id=5)
    service.close_chat.return_value = True
    message = make_message("/close")

    await chat_handlers.cmd_close_chat(message, state, service)

    service.close_chat.assert_awaited_once_with(TEST_TELEGRAM_ID, 5)
    assert message.answer.await_args.args[0] == texts.CHAT_CLOSED
    assert await state.get_state() is None


async def test_close_without_dialog_shows_neutral_text() -> None:
    state = make_state()
    message = make_message("/close")
    service = AsyncMock(spec=ChatService)
    service.get_active_chat.return_value = None

    await chat_handlers.cmd_close_chat(message, state, service)

    assert message.answer.await_args.args[0] == texts.CHAT_NOT_FOUND


async def test_history_renders_last_n_from_config() -> None:
    state = make_state()
    await activate(state)
    message = make_message("/history")
    service = AsyncMock(spec=ChatService)
    service.get_active_chat.return_value = SimpleNamespace(id=5)
    messages = [
        SimpleNamespace(role="user", text=f"msg {number}", id=number)
        for number in range(12)
    ]
    service.get_history.return_value = messages[-3:]  # service applies limit

    await chat_handlers.cmd_history(message, state, service, SETTINGS)

    service.get_history.assert_awaited_once_with(
        TEST_TELEGRAM_ID, 5, limit=SETTINGS.chat_history_last_n
    )
    rendered = message.answer.await_args.args[0]
    assert texts.CHAT_HISTORY_TITLE in rendered
    assert "msg 9" in rendered and "msg 11" in rendered


async def test_history_empty_shows_hint() -> None:
    state = make_state()
    message = make_message("/history")
    service = AsyncMock(spec=ChatService)
    service.get_active_chat.return_value = None

    await chat_handlers.cmd_history(message, state, service, SETTINGS)

    assert message.answer.await_args.args[0] == texts.CHAT_HISTORY_EMPTY


async def test_non_text_message_gets_hint_without_llm() -> None:
    state = make_state()
    await activate(state)
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    del message.text  # F.text filter requires no text value

    await chat_handlers.on_chat_non_text(message)

    message.answer.assert_awaited_once_with(texts.CHAT_NON_TEXT)


# ---------------------------------------------------------------------------
# Blocker 1: key format survives the Idempotency-Key contract
# ---------------------------------------------------------------------------


async def test_deterministic_key_passes_idempotency_contract() -> None:
    """update_id=1 produces a key valid for the shared contract
    (8-64 chars of [A-Za-z0-9_-]), not just for the Telegram path."""
    from app.bot.handlers.chat import _client_request_id

    key = _client_request_id({"tg_update_id": 1})
    assert key == "tg-update-1"
    assert len(key) >= 8
    assert _IDEMPOTENCY_KEY_PATTERN.fullmatch(key) is not None
    # And the dependency itself accepts it.
    assert await get_idempotency_key(idempotency_key=key) == key


# ---------------------------------------------------------------------------
# Blocker 2: friendly texts for every reason, no reason leakage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    sorted(texts.CHAT_ERROR_TEXTS),
)
async def test_every_reason_maps_to_friendly_text_without_leak(
    reason: str,
) -> None:
    state = make_state()
    await activate(state)
    from app.services.tutor.chat import ChatReason

    service = make_service(ChatUnavailable(reason=cast(ChatReason, reason)))
    message = make_message()
    message = make_message()

    await chat_handlers.on_chat_message(
        message, state, service, make_data(update_id=777)
    )

    answer = message.answer.await_args.args[0]
    assert answer == texts.CHAT_ERROR_TEXTS[reason]
    # The internal reason code must not leak into the answer
    # (case-insensitive) — a future f"Ошибка: {reason}" fails here.
    assert reason.lower() not in answer.lower()


def test_all_expected_reasons_are_covered() -> None:
    expected = {
        "busy",
        "rate_limited",
        "claim_lost",
        "message_too_long",
        "conversation_closed",
        "conversation_not_found",
        "llm_disabled",
        "llm_error",
        "invalid_output",
    }
    assert expected.issubset(set(texts.CHAT_ERROR_TEXTS))


# ---------------------------------------------------------------------------
# Blocker 3: ownership in handlers
# ---------------------------------------------------------------------------


async def test_foreign_user_gets_neutral_text_and_no_send() -> None:
    """User B (another telegram id) writes into user A's open chat:
    neutral text, no send_message call (=> no LLM call)."""
    state = make_state()
    await activate(state)
    service = AsyncMock(spec=ChatService)
    # B has no active dialog of their own (repo is user-scoped: a
    # foreign conversation_id is indistinguishable from missing).
    service.get_active_chat.return_value = None
    message = make_message("intrude into chat A")

    await chat_handlers.on_chat_message(
        message, state, service, make_data(update_id=900)
    )

    assert message.answer.await_args.args[0] == texts.CHAT_NOT_FOUND
    service.send_message.assert_not_awaited()
    assert await state.get_state() is None


async def test_close_of_foreign_chat_is_neutral_without_llm() -> None:
    """User B runs /close against user A's dialog: neutral text,
    close_chat not called (=> no side effects, no LLM call)."""
    state = make_state()
    await activate(state)
    service = AsyncMock(spec=ChatService)
    service.get_active_chat.return_value = None
    message = make_message("/close")

    await chat_handlers.cmd_close_chat(message, state, service)

    service.close_chat.assert_not_awaited()
    assert message.answer.await_args.args[0] == texts.CHAT_NOT_FOUND
