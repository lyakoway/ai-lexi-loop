"""Repositories for tutor conversations and messages (Stage 6.4.1).

Basic storage and reading only: creation, user-scoped lookups, history
and idempotency-key lookups. Claim lifecycle, fencing, rate limiting and
idempotency orchestration are implemented by ChatService in Stage 6.4.2.
Every read is user-scoped so foreign conversations and messages are
indistinguishable from non-existent ones.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TutorConversation, TutorMessage
from app.domain.srs import utc_now
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

    async def try_claim(
        self,
        *,
        user_id: int,
        conversation_id: int,
        claim_id: uuid.UUID,
        min_interval_seconds: float,
        claim_timeout_seconds: float,
        now: datetime,
    ) -> str:
        """Atomically claim generation for a conversation.

        Returns ``claimed``, or why the claim was refused: ``busy``
        (another generation holds a fresh claim), ``rate_limited`` (the
        previous attempt — successful or not — is too recent),
        ``closed`` or ``not_found``. The pre-check determines the
        reported reason; the conditional UPDATE stays atomic, so a
        parallel claim winning the race also reports ``busy``.
        """
        async with wrap_storage_errors():
            state = await self._session.execute(
                select(
                    TutorConversation.status,
                    TutorConversation.generation_started_at,
                    TutorConversation.last_generation_at,
                ).where(
                    TutorConversation.id == conversation_id,
                    TutorConversation.user_id == user_id,
                )
            )
            row = state.first()
            if row is None:
                return "not_found"
            status, generation_started_at, last_generation_at = row
            if status != "active":
                return "closed"
            if (
                generation_started_at is not None
                and generation_started_at
                > now - timedelta(seconds=claim_timeout_seconds)
            ):
                return "busy"
            if last_generation_at is not None and last_generation_at > now - timedelta(
                seconds=min_interval_seconds
            ):
                return "rate_limited"

            result = await self._session.execute(
                update(TutorConversation)
                .where(
                    TutorConversation.id == conversation_id,
                    TutorConversation.user_id == user_id,
                    TutorConversation.status == "active",
                    or_(
                        TutorConversation.generation_started_at.is_(None),
                        TutorConversation.generation_started_at
                        <= now - timedelta(seconds=claim_timeout_seconds),
                    ),
                    or_(
                        TutorConversation.last_generation_at.is_(None),
                        TutorConversation.last_generation_at
                        <= now - timedelta(seconds=min_interval_seconds),
                    ),
                )
                .values(
                    generation_started_at=now,
                    generation_claim_id=claim_id,
                    last_generation_at=now,
                )
                .returning(TutorConversation.id)
            )
            if result.first() is None:
                # The race was won by a parallel claim.
                return "busy"
            return "claimed"

    async def release_claim(
        self,
        *,
        user_id: int,
        conversation_id: int,
        claim_id: uuid.UUID,
    ) -> bool:
        """Release the claim only if this request still owns it (fencing).

        ``False`` means the claim was already stolen by another request
        (its timeout elapsed) — touching it now would be destructive.
        """
        async with wrap_storage_errors():
            result = await self._session.execute(
                update(TutorConversation)
                .where(
                    TutorConversation.id == conversation_id,
                    TutorConversation.user_id == user_id,
                    TutorConversation.generation_claim_id == claim_id,
                )
                .values(generation_started_at=None, generation_claim_id=None)
                .returning(TutorConversation.id)
            )
            return result.first() is not None

    async def close(self, *, user_id: int, conversation_id: int) -> bool:
        """Close an active conversation owned by the user."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                update(TutorConversation)
                .where(
                    TutorConversation.id == conversation_id,
                    TutorConversation.user_id == user_id,
                    TutorConversation.status == "active",
                )
                .values(status="closed", closed_at=utc_now())
                .returning(TutorConversation.id)
            )
            return result.first() is not None

    async def delete(self, *, user_id: int, conversation_id: int) -> bool:
        """Delete a conversation owned by the user (messages cascade)."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TutorConversation).where(
                    TutorConversation.id == conversation_id,
                    TutorConversation.user_id == user_id,
                )
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                return False
            await self._session.delete(conversation)
            await self._session.flush()
            return True


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
        self,
        *,
        user_id: int,
        conversation_id: int,
        limit: int | None = None,
    ) -> list[TutorMessage]:
        """History of an owned conversation in chronological order.

        Ownership is enforced through a join with the conversation.
        ``limit`` keeps only the last N messages (order preserved).
        """
        async with wrap_storage_errors():
            query = (
                select(TutorMessage)
                .join(
                    TutorConversation,
                    TutorConversation.id == TutorMessage.conversation_id,
                )
                .where(
                    TutorMessage.conversation_id == conversation_id,
                    TutorConversation.user_id == user_id,
                )
            )
            if limit is not None:
                newest = await self._session.execute(
                    query.order_by(
                        TutorMessage.created_at.desc(), TutorMessage.id.desc()
                    ).limit(limit)
                )
                messages = list(newest.scalars().all())
                messages.reverse()
                return messages
            result = await self._session.execute(
                query.order_by(TutorMessage.created_at, TutorMessage.id)
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

    async def finalize_exchange(
        self,
        *,
        user_id: int,
        conversation_id: int,
        claim_id: uuid.UUID,
        user_text: str,
        assistant_text: str,
        client_request_id: str | None,
        prompt_version: str,
        max_messages: int,
    ) -> TutorMessage | None:
        """Atomically save the (user, assistant) pair, bump the counter,
        auto-close at the limit and release the claim.

        Fencing: the conversation UPDATE matches the ``claim_id``, so a
        request whose claim was stolen (timeout elapsed and another
        request took over) changes nothing and ``None`` is returned —
        the winner's state stays untouched.
        """
        async with wrap_storage_errors():
            try:
                async with self._session.begin_nested():
                    conversation = await self._session.execute(
                        select(TutorConversation)
                        .where(
                            TutorConversation.id == conversation_id,
                            TutorConversation.user_id == user_id,
                            TutorConversation.generation_claim_id == claim_id,
                        )
                        .with_for_update()
                    )
                    row = conversation.scalar_one_or_none()
                    if row is None:
                        # Fencing: the claim was stolen after its timeout
                        # — the savepoint rolls back everything below.
                        raise _ClaimLostError()

                    # Clamp keeps the counter exactly at the limit even
                    # if a non-even count ever appears (defensive; the
                    # service always adds pairs, so counts stay even).
                    new_count = min(row.message_count + 2, max_messages)
                    user_message = TutorMessage(
                        conversation_id=conversation_id,
                        role="user",
                        text=user_text,
                        client_request_id=client_request_id,
                    )
                    self._session.add(user_message)
                    await self._session.flush()
                    self._session.add(
                        TutorMessage(
                            conversation_id=conversation_id,
                            role="assistant",
                            text=assistant_text,
                            prompt_version=prompt_version,
                            in_reply_to_message_id=user_message.id,
                        )
                    )
                    # Close as soon as the pair reaches the limit, and
                    # release the claim. Explicit UPDATE: deterministic
                    # counter/close/release in one statement, fenced by
                    # the claim id (the savepoint rolls the whole block
                    # back if the claim was stolen).
                    closing = new_count >= max_messages
                    await self._session.execute(
                        update(TutorConversation)
                        .where(
                            TutorConversation.id == conversation_id,
                            TutorConversation.user_id == user_id,
                            TutorConversation.generation_claim_id == claim_id,
                        )
                        .values(
                            message_count=new_count,
                            status="closed" if closing else "active",
                            closed_at=utc_now() if closing else None,
                            generation_started_at=None,
                            generation_claim_id=None,
                        )
                    )
            except _ClaimLostError:
                return None
            return user_message


class _ClaimLostError(Exception):
    """Internal: the fencing claim belongs to another request."""
