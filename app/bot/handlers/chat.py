"""Telegram flow of the AI Tutor Chat (Stage 6.4.4).

``/chat`` opens (or resumes) the active dialog; text messages inside the
chat state go to ``ChatService.send_message`` with a deterministic
idempotency key derived from the Telegram ``update_id`` — so a retried
delivery replays the saved answer instead of paying for a second LLM
call. Errors map to friendly texts without internal reason codes.
"""

import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.bot import texts
from app.services.tutor import ChatService, ChatUnavailable

logger = logging.getLogger(__name__)

router = Router(name="chat")


class ChatFlow(StatesGroup):
    """FSM state marking an open tutor chat for this user."""

    active = State()


def _friendly_text(reason: str) -> str:
    """Map a ChatService reason to a user-facing text (no internals).

    Renders strictly from ``texts.CHAT_ERROR_TEXTS``: the reason string
    itself must never leak into the answer (enforced by a parametrized
    test over every reason).
    """
    return texts.CHAT_ERROR_TEXTS.get(reason, texts.CHAT_UNAVAILABLE)


def _client_request_id(data: dict[str, Any]) -> str | None:
    """Deterministic idempotency key from the Telegram update id.

    Prefix ``tg-update-`` serves three purposes: (1) determinism — a
    retried Telegram delivery carries the same update_id and replays the
    saved answer instead of paying for a second LLM call; (2) length
    guarantee — ``tg-update-{id}`` is always >= 11 chars, satisfying the
    shared Idempotency-Key contract (8-64 chars of [A-Za-z0-9_-]) even
    for update_id=1; (3) channel namespacing — future channels use their
    own ``{channel}-{id}`` prefix with the same validation.
    """
    update_id = data.get("tg_update_id")
    return f"tg-update-{update_id}" if update_id is not None else None


def _escape(text_value: str) -> str:
    return html.escape(text_value, quote=False)


def _render_reply(reply: Any) -> str:
    """Render a ChatReplyOut into Telegram HTML (all model text escaped)."""
    parts = [f"{texts.CHAT_REPLY_PREFIX} {_escape(reply.reply)}"]
    if reply.correction:
        parts.append(f"{texts.CHAT_CORRECTION_TITLE} {_escape(reply.correction)}")
    if reply.correction_note:
        parts.append(
            f"{texts.CHAT_CORRECTION_NOTE_TITLE} {_escape(reply.correction_note)}"
        )
    if reply.better_alternative:
        parts.append(
            f"{texts.CHAT_ALTERNATIVE_TITLE} {_escape(reply.better_alternative)}"
        )
    if reply.follow_up_question:
        parts.append(f"{texts.CHAT_FOLLOWUP_TITLE} {_escape(reply.follow_up_question)}")
    return "\n".join(parts)


def _render_history(messages: list[Any]) -> str:
    lines = [
        f"{'👤' if message.role == 'user' else '🤖'} {_escape(message.text)}"
        for message in messages
    ]
    return f"{texts.CHAT_HISTORY_TITLE}\n" + "\n".join(lines)


@router.message(Command("chat"))
async def cmd_chat(
    message: Message,
    state: FSMContext,
    chat_service: ChatService,
    data: dict[str, Any],
) -> None:
    """Open or resume the tutor chat dialog and greet the user."""
    if message.from_user is None:
        return
    conversation = await chat_service.start_chat(
        message.from_user.id, source="telegram"
    )
    if isinstance(conversation, ChatUnavailable):
        logger.warning(
            "chat start unavailable user_id=%s reason=%s",
            message.from_user.id,
            conversation.reason,
        )
        await message.answer(texts.CHAT_UNAVAILABLE)
        return

    await state.set_state(ChatFlow.active)
    if conversation.message_count > 0:
        await message.answer(
            texts.CHAT_RESUMED.format(count=conversation.message_count)
        )
    else:
        topic = conversation.topic or "свободная практика"
        await message.answer(texts.CHAT_STARTED.format(topic=html.escape(topic)))


@router.message(Command("close"), ChatFlow.active)
async def cmd_close_chat(
    message: Message,
    state: FSMContext,
    chat_service: ChatService,
) -> None:
    """Close the active tutor chat dialog."""
    if message.from_user is None:
        return
    conversation = await chat_service.get_active_chat(message.from_user.id)
    if conversation is None:
        await state.clear()
        await message.answer(texts.CHAT_NOT_FOUND)
        return
    closed = await chat_service.close_chat(message.from_user.id, conversation.id)
    await state.clear()
    if closed:
        await message.answer(texts.CHAT_CLOSED)
    else:
        await message.answer(texts.CHAT_CLOSED)


@router.message(Command("history"), ChatFlow.active)
async def cmd_history(
    message: Message,
    state: FSMContext,
    chat_service: ChatService,
    settings: Any,
) -> None:
    """Show the last N messages of the dialog (N from config, default 10)."""
    if message.from_user is None:
        return
    conversation = await chat_service.get_active_chat(message.from_user.id)
    if conversation is None:
        await message.answer(texts.CHAT_HISTORY_EMPTY)
        return
    messages = await chat_service.get_history(
        message.from_user.id,
        conversation.id,
        limit=settings.chat_history_last_n,
    )
    if not messages:
        await message.answer(texts.CHAT_HISTORY_EMPTY)
        return
    await message.answer(_render_history(messages))


@router.message(StateFilter(ChatFlow.active), F.text)
async def on_chat_message(
    message: Message,
    state: FSMContext,
    chat_service: ChatService,
    data: dict[str, Any],
) -> None:
    """Send a user message to the tutor and render the structured reply."""
    if message.from_user is None or message.text is None:
        return
    conversation = await chat_service.get_active_chat(message.from_user.id)
    if conversation is None:
        await state.clear()
        await message.answer(texts.CHAT_NOT_FOUND)
        return

    client_request_id = _client_request_id(data)
    result = await chat_service.send_message(
        message.from_user.id,
        conversation.id,
        message.text,
        client_request_id=client_request_id,
        source="telegram",
    )
    if isinstance(result, ChatUnavailable):
        await message.answer(_friendly_text(result.reason))
        # The dialog is gone (closed or deleted): leaving the chat mode.
        if result.reason in ("conversation_closed", "conversation_not_found"):
            await state.clear()
        return

    await message.answer(_render_reply(result.reply))
    if result.closed:
        await state.clear()
        await message.answer(texts.CHAT_CLOSED)


@router.message(StateFilter(ChatFlow.active))
async def on_chat_non_text(message: Message) -> None:
    """Non-text content in an open chat: hint, no LLM call."""
    await message.answer(texts.CHAT_NON_TEXT)
