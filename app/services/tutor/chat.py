"""AI Tutor Chat service (Stage 6.4.2): controlled multi-turn dialog.

Transaction boundaries are explicit and short — the claim is a single
atomic UPDATE, the LLM call runs outside any transaction, and the
(user, assistant) pair is saved in one final transaction guarded by the
fencing claim UUID. A stolen claim (its timeout elapsed and another
request took over) can neither save messages nor release the winner's
claim. The service owns no long-lived state: conversations and messages
live in PostgreSQL.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings
from app.db.models import TutorConversation, TutorMessage
from app.db.session import SessionFactory, session_scope
from app.domain.srs import utc_now
from app.integrations.llm.client import StructuredLlmClient
from app.integrations.llm.exceptions import LlmError, LlmOutputFormatError
from app.integrations.llm.schemas import LlmMessage, LlmRequest
from app.repositories import AnalyticsRepository, ProfileRepository
from app.repositories.chat import (
    TutorConversationRepository,
    TutorMessageRepository,
)
from app.services.analytics import AnalyticsService, EventSource
from app.services.tutor.chat_context import build_chat_context
from app.services.tutor.chat_outputs import ChatReplyOut
from app.services.tutor.prompts import CHAT_PROMPT_VERSION

logger = logging.getLogger(__name__)

MAX_CHAT_MESSAGE_CHARS = 1000
MAX_CHAT_MESSAGES_PER_CONVERSATION = 100

ChatReason = Literal[
    "profile_not_found",
    "conversation_not_found",
    "conversation_closed",
    "busy",
    "rate_limited",
    "claim_lost",
    "message_too_long",
    "llm_disabled",
    "llm_error",
    "invalid_output",
    "idempotency_conflict",
    "idempotency_pending",
]


@dataclass(frozen=True)
class ChatExchange:
    """A completed chat exchange with its persisted messages."""

    conversation: TutorConversation
    user_message: TutorMessage
    assistant_message: TutorMessage | None
    reply: ChatReplyOut
    closed: bool
    replay: bool = False


@dataclass(frozen=True)
class ChatUnavailable:
    """Why the exchange could not happen (typed, no internals)."""

    reason: ChatReason


class ChatService:
    """Multi-turn AI tutor chat over TutorConversation/TutorMessage."""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm_client: StructuredLlmClient | None,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._llm_client = llm_client
        self._settings = settings

    async def start_chat(
        self,
        user_id: int,
        *,
        topic: str | None = None,
        situation: str | None = None,
        source: EventSource = "telegram",
    ) -> TutorConversation | ChatUnavailable:
        """Create or resume the user's active chat dialog."""
        async with session_scope(self._session_factory) as session:
            profile = await ProfileRepository(session).get_by_user_id(user_id)
            if profile is None:
                return ChatUnavailable(reason="profile_not_found")

            conversation_repo = TutorConversationRepository(session)
            existing = await conversation_repo.get_active_for_user(user_id)
            if existing is not None:
                return existing

            conversation = await conversation_repo.create(
                user_id=user_id,
                language=profile.target_language,
                native_language=profile.native_language,
                level=profile.level,
                topic=topic,
                situation=situation,
            )
            await AnalyticsService(AnalyticsRepository(session)).track(
                user_id,
                "tutor_chat_started",
                source,
                {"topic": topic or ""},
            )
            logger.info(
                "chat started user_id=%s conversation=%s",
                user_id,
                conversation.id,
            )
            return conversation

    async def send_message(
        self,
        user_id: int,
        conversation_id: int,
        text: str,
        *,
        client_request_id: str | None = None,
        source: EventSource = "telegram",
    ) -> ChatExchange | ChatUnavailable:
        """Handle one user message; returns the exchange or a typed reason."""
        if len(text) > MAX_CHAT_MESSAGE_CHARS:
            return ChatUnavailable(reason="message_too_long")

        # Idempotency replay (owned data only) — before claiming.
        async with session_scope(self._session_factory) as session:
            message_repo = TutorMessageRepository(session)
            existing = (
                await message_repo.get_user_message_by_client_request_id(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    client_request_id=client_request_id,
                )
                if client_request_id
                else None
            )
            if existing is not None:
                return await self._resolve_idempotent(
                    session=session,
                    message_repo=message_repo,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    existing=existing,
                    text=text,
                )

        # Claim: atomic, with rate limiting and a fencing UUID.
        claim_id = uuid.uuid4()
        async with session_scope(self._session_factory) as session:
            claim = await TutorConversationRepository(session).try_claim(
                user_id=user_id,
                conversation_id=conversation_id,
                claim_id=claim_id,
                min_interval_seconds=(self._settings.llm_chat_min_interval_seconds),
                claim_timeout_seconds=(self._settings.chat_claim_timeout_seconds),
                now=utc_now(),
            )
        if claim != "claimed":
            await self._track_best_effort(
                user_id,
                "tutor_chat_message" if claim in ("busy", "rate_limited") else claim,
                source,
                {"success": False, "error": claim},
            )
            if claim == "busy":
                await self._track_best_effort(user_id, "tutor_chat_busy", source, {})
            if claim == "closed":
                return ChatUnavailable(reason="conversation_closed")
            if claim == "not_found":
                return ChatUnavailable(reason="conversation_not_found")
            claim_reason: ChatReason = "busy" if claim == "busy" else "rate_limited"
            return ChatUnavailable(reason=claim_reason)

        # Load the minimal context (owned conversation only).
        async with session_scope(self._session_factory) as session:
            conversation = await TutorConversationRepository(session).get_for_user(
                user_id=user_id, conversation_id=conversation_id
            )
            if conversation is None:
                await self._release(user_id, conversation_id, claim_id)
                return ChatUnavailable(reason="conversation_not_found")
            if conversation.status != "active":
                await self._release(user_id, conversation_id, claim_id)
                return ChatUnavailable(reason="conversation_closed")
            history = await TutorMessageRepository(session).list_for_conversation(
                user_id=user_id, conversation_id=conversation_id
            )
            history_entries = [(message.role, message.text) for message in history]
            context_native = conversation.native_language
            context_target = conversation.language
            context_level = conversation.level
            context_topic: str | None = conversation.topic
            context_situation: str | None = conversation.situation

        if self._llm_client is None:
            await self._release(user_id, conversation_id, claim_id)
            await self._track_best_effort(
                user_id,
                "tutor_chat_message",
                source,
                {"success": False, "error": "llm_disabled"},
            )
            return ChatUnavailable(reason="llm_disabled")

        system_prompt, user_prompt = build_chat_context(
            history=history_entries,
            new_user_message=text,
            native_language=context_native,
            target_language=context_target,
            level=context_level,
            topic=context_topic,
            situation=context_situation,
        )
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=system_prompt),
                LlmMessage(role="user", content=user_prompt),
            ],
            prompt_version=CHAT_PROMPT_VERSION,
        )

        try:
            reply = await self._llm_client.complete_structured(request, ChatReplyOut)
        except LlmOutputFormatError:
            logger.warning(
                "chat output invalid user_id=%s conversation=%s",
                user_id,
                conversation_id,
            )
            await self._release(user_id, conversation_id, claim_id)
            await self._track_best_effort(
                user_id,
                "tutor_chat_message",
                source,
                {"success": False, "error": "invalid_output"},
            )
            return ChatUnavailable(reason="invalid_output")
        except LlmError as error:
            logger.warning(
                "chat llm error user_id=%s conversation=%s error_type=%s",
                user_id,
                conversation_id,
                type(error).__name__,
            )
            await self._release(user_id, conversation_id, claim_id)
            await self._track_best_effort(
                user_id,
                "tutor_chat_message",
                source,
                {"success": False, "error": "llm_error"},
            )
            return ChatUnavailable(reason="llm_error")

        # Final transaction: pair + counter + auto-close + claim release,
        # guarded by fencing (None → claim stolen: keep the winner's data).
        async with session_scope(self._session_factory) as session:
            user_message = await TutorMessageRepository(session).finalize_exchange(
                user_id=user_id,
                conversation_id=conversation_id,
                claim_id=claim_id,
                user_text=text,
                assistant_text=reply.reply,
                client_request_id=client_request_id,
                prompt_version=CHAT_PROMPT_VERSION,
                max_messages=MAX_CHAT_MESSAGES_PER_CONVERSATION,
            )
            if user_message is None:
                logger.warning(
                    "chat claim lost user_id=%s conversation=%s",
                    user_id,
                    conversation_id,
                )
                # The savepoint rollback already discarded the pair.

        if user_message is None:
            # Telemetry for the lost claim goes out in its own short
            # sessions after the rollback (best-effort).
            await self._track_best_effort(user_id, "tutor_chat_claim_lost", source, {})
            await self._track_best_effort(
                user_id,
                "tutor_chat_message",
                source,
                {"success": False, "error": "claim_lost"},
            )
            return ChatUnavailable(reason="claim_lost")

        # Success telemetry is best-effort too: an analytics failure must
        # never turn a saved exchange into an error for the user.
        await self._track_best_effort(
            user_id,
            "tutor_chat_message",
            source,
            {
                "success": True,
                "message_chars_bucket": min(len(text), 1000),
                "replay": False,
            },
        )

        # Fetch the persisted assistant message via the pair link.
        async with session_scope(self._session_factory) as session:
            assistant_message = await TutorMessageRepository(
                session
            ).get_assistant_reply(
                user_id=user_id, in_reply_to_message_id=user_message.id
            )
        if assistant_message is None:
            logger.error(
                "chat assistant message missing after finalize user_id=%s",
                user_id,
            )
            return ChatUnavailable(reason="conversation_not_found")

        conversation_now = await self._get_conversation(user_id, conversation_id)
        if conversation_now is None:
            logger.error(
                "chat conversation disappeared after finalize user_id=%s",
                user_id,
            )
            return ChatUnavailable(reason="conversation_not_found")

        logger.info(
            "chat exchange ok user_id=%s conversation=%s", user_id, conversation_id
        )
        return ChatExchange(
            conversation=conversation_now,
            user_message=user_message,
            assistant_message=assistant_message,
            reply=reply,
            closed=conversation_now.status == "closed",
        )

    async def _resolve_idempotent(
        self,
        *,
        session: Any,
        message_repo: Any,
        user_id: int,
        conversation_id: int,
        existing: TutorMessage,
        text: str,
    ) -> ChatExchange | ChatUnavailable:
        """Resolve a repeated Idempotency-Key (before any claim)."""
        if existing.text != text:
            return ChatUnavailable(reason="idempotency_conflict")
        reply_message = await message_repo.get_assistant_reply(
            user_id=user_id, in_reply_to_message_id=existing.id
        )
        if reply_message is None:
            return ChatUnavailable(reason="idempotency_pending")
        conversation = await TutorConversationRepository(session).get_for_user(
            user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return ChatUnavailable(reason="conversation_not_found")
        logger.info("chat replay user_id=%s conversation=%s", user_id, conversation_id)
        return ChatExchange(
            conversation=conversation,
            user_message=existing,
            assistant_message=reply_message,
            reply=ChatReplyOut(reply=reply_message.text),
            closed=conversation.status == "closed",
            replay=True,
        )

    async def close_chat(self, user_id: int, conversation_id: int) -> bool:
        """Close the user's active dialog; False for foreign/missing."""
        async with session_scope(self._session_factory) as session:
            return await TutorConversationRepository(session).close(
                user_id=user_id, conversation_id=conversation_id
            )

    async def delete_chat(self, user_id: int, conversation_id: int) -> bool:
        """Delete the user's dialog with its messages."""
        async with session_scope(self._session_factory) as session:
            return await TutorConversationRepository(session).delete(
                user_id=user_id, conversation_id=conversation_id
            )

    async def list_chats(self, user_id: int) -> list[TutorConversation]:
        """All dialogs of the user, newest first."""
        async with session_scope(self._session_factory) as session:
            return await TutorConversationRepository(session).list_for_user(user_id)

    async def get_history(
        self, user_id: int, conversation_id: int
    ) -> list[TutorMessage]:
        """Messages of the user's dialog, chronological."""
        async with session_scope(self._session_factory) as session:
            return await TutorMessageRepository(session).list_for_conversation(
                user_id=user_id, conversation_id=conversation_id
            )

    async def _release(
        self, user_id: int, conversation_id: int, claim_id: uuid.UUID
    ) -> None:
        """Release the claim after an LLM failure (fencing-aware)."""
        async with session_scope(self._session_factory) as session:
            released = await TutorConversationRepository(session).release_claim(
                user_id=user_id,
                conversation_id=conversation_id,
                claim_id=claim_id,
            )
            if not released:
                logger.warning(
                    "chat release skipped (claim stolen) user_id=%s", user_id
                )

    async def _track_best_effort(
        self,
        user_id: int,
        event_name: str,
        source: EventSource,
        properties: dict[str, str | int | float | bool | None],
    ) -> None:
        """Record an analytics event in its own short session.

        Best-effort: a database failure here is logged and never changes
        the operation result shown to the user.
        """
        try:
            async with session_scope(self._session_factory) as session:
                await AnalyticsService(AnalyticsRepository(session)).track(
                    user_id, event_name, source, properties
                )
        except Exception:
            logger.warning(
                "chat analytics write failed user_id=%s event=%s",
                user_id,
                event_name,
            )

    async def _get_conversation(
        self, user_id: int, conversation_id: int
    ) -> TutorConversation | None:
        async with session_scope(self._session_factory) as session:
            return await TutorConversationRepository(session).get_for_user(
                user_id=user_id, conversation_id=conversation_id
            )
