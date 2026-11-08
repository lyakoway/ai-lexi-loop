"""Repositories for tutor conversations and messages (Stage 6.4.1).

Basic storage and reading only: creation, user-scoped lookups, history
and idempotency-key lookups. Claim lifecycle, fencing, rate limiting and
idempotency orchestration are implemented by ChatService in Stage 6.4.2.
Every read is user-scoped so foreign conversations and messages are
indistinguishable from non-existent ones.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TutorConversation, TutorMessage
from app.repositories.errors import wrap_storage_errors


class TutorConversationRepository:
    """Data access for tutor conversations; every read is user-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        language: str,
        native_language: str,
        level: str,
        topic: str | None = None,
        situation: str | None = None,
    ) -> TutorConversation:
        """Insert a new active conversation (flushed, not committed)."""
        async with wrap_storage_errors():
            conversation = TutorConversation(
                user_id=user_id,
                language=language,
                native_language=native_language,
                level=level,
                topic=topic,
                situation=situation,
            )
            self._session.add(conversation)
            await self._session.flush()
            await self._session.refresh(conversation)
            return conversation

    async def get_for_user(
        self, *, user_id: int, conversation_id: int
    ) -> TutorConversation | None:
        """Return the user's conversation by id, else ``None``.

        A foreign or missing id yields the same ``None`` — no
        information leak about existence.
        """
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TutorConversation).where(
                    TutorConversation.id == conversation_id,
                    TutorConversation.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: int) -> TutorConversation | None:
        """The user's active conversation, or ``None``."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TutorConversation).where(
                    TutorConversation.user_id == user_id,
                    TutorConversation.status == "active",
                )
            )
            return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[TutorConversation]:
        """All conversations of the user, newest first."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TutorConversation)
                .where(TutorConversation.user_id == user_id)
                .order_by(
                    TutorConversation.created_at.desc(), TutorConversation.id.desc()
                )
            )
            return list(result.scalars().all())


class TutorMessageRepository:
    """Data access for tutor messages.

    Reads take ``user_id`` and verify ownership through a join with the
    conversation, so a foreign conversation id yields no messages.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        conversation_id: int,
        role: str,
        text: str,
        client_request_id: str | None = None,
        in_reply_to_message_id: int | None = None,
        prompt_version: str | None = None,
        error_kind: str | None = None,
    ) -> TutorMessage:
        """Insert one message (flushed, not committed)."""
        async with wrap_storage_errors():
            message = TutorMessage(
                conversation_id=conversation_id,
                role=role,
                text=text,
                client_request_id=client_request_id,
                in_reply_to_message_id=in_reply_to_message_id,
                prompt_version=prompt_version,
                error_kind=error_kind,
            )
            self._session.add(message)
            await self._session.flush()
            await self._session.refresh(message)
            return message

    async def list_for_conversation(
        self, *, user_id: int, conversation_id: int
    ) -> list[TutorMessage]:
        """History of an owned conversation in chronological order.

        Ownership is enforced through a join with the conversation.
        """
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TutorMessage)
                .join(
                    TutorConversation,
                    TutorConversation.id == TutorMessage.conversation_id,
                )
                .where(
                    TutorMessage.conversation_id == conversation_id,
                    TutorConversation.user_id == user_id,
                )
                .order_by(TutorMessage.created_at, TutorMessage.id)
            )
            return list(result.scalars().all())

    async def get_user_message_by_client_request_id(
        self, *, user_id: int, conversation_id: int, client_request_id: str
    ) -> TutorMessage | None:
        """The user message with this idempotency key, or ``None``."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TutorMessage)
                .join(
                    TutorConversation,
                    TutorConversation.id == TutorMessage.conversation_id,
                )
                .where(
                    TutorMessage.conversation_id == conversation_id,
                    TutorMessage.role == "user",
                    TutorMessage.client_request_id == client_request_id,
                    TutorConversation.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_assistant_reply(
        self, *, user_id: int, in_reply_to_message_id: int
    ) -> TutorMessage | None:
        """The assistant reply linked to the given user message, or ``None``."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TutorMessage)
                .join(
                    TutorConversation,
                    TutorConversation.id == TutorMessage.conversation_id,
                )
                .where(
                    TutorMessage.role == "assistant",
                    TutorMessage.in_reply_to_message_id == in_reply_to_message_id,
                    TutorConversation.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()
