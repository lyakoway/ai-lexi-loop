"""Integration tests for tutor chat models and repositories (Stage 6.4.1).

Real PostgreSQL: CHECK constraints, partial unique indexes, the
composite self-FK of the message pair link, repository ownership, and
the up/down/up migration cycle.
"""

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TutorConversation, TutorMessage
from app.db.session import session_scope
from app.repositories import (
    TutorConversationRepository,
    TutorMessageRepository,
)
from tests.integration.conftest import (
    create_profile_row,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


def make_conversation_repo(session: AsyncSession) -> TutorConversationRepository:
    return TutorConversationRepository(session)


def make_message_repo(session: AsyncSession) -> TutorMessageRepository:
    return TutorMessageRepository(session)


async def prepare_user(session_factory: Any, telegram_id: int) -> int:
    """Create a user and return its internal id."""
    async with session_scope(session_factory) as session:
        profile = await create_profile_row(session, telegram_id=telegram_id)
        return profile.user_id


async def create_conversation(
    session_factory: Any,
    user_id: int,
    *,
    status: str = "active",
    message_count: int = 0,
) -> int:
    async with session_scope(session_factory) as session:
        conversation = TutorConversation(
            user_id=user_id,
            language="ts",
            native_language="ru",
            level="A1",
            topic="everyday",
            status=status,
            message_count=message_count,
        )
        session.add(conversation)
        await session.flush()
        return conversation.id


async def add_message(
    session_factory: Any,
    conversation_id: int,
    *,
    role: str,
    text: str,
    client_request_id: str | None = None,
    in_reply_to_message_id: int | None = None,
) -> int:
    async with session_scope(session_factory) as session:
        message = TutorMessage(
            conversation_id=conversation_id,
            role=role,
            text=text,
            client_request_id=client_request_id,
            in_reply_to_message_id=in_reply_to_message_id,
        )
        session.add(message)
        await session.flush()
        return message.id


# ---------------------------------------------------------------------------
# Creation, CHECK constraints, composite FK
# ---------------------------------------------------------------------------


async def test_conversation_create_and_read_back(
    migrated_db: None, session_factory: Any
) -> None:
    user_id = await prepare_user(session_factory, 9200)
    async with session_scope(session_factory) as session:
        created = await make_conversation_repo(session).create(
            user_id=user_id,
            language="ts",
            native_language="ru",
            level="A1",
            topic="everyday",
            situation=" ordering coffee",
        )
        assert created.id is not None
        assert created.status == "active"
        assert created.message_count == 0
        assert created.generation_claim_id is None
    async with session_scope(session_factory) as session:
        loaded = await make_conversation_repo(session).get_for_user(
            user_id=user_id, conversation_id=created.id
        )
        assert loaded is not None
        assert loaded.situation == " ordering coffee"
        assert loaded.topic == "everyday"


async def test_conversation_check_level_and_status(
    migrated_db: None, session_factory: Any
) -> None:
    user_id = await prepare_user(session_factory, 9201)
    for bad in ({"level": "Z9"}, {"status": "paused"}):
        async with session_scope(session_factory) as session:
            values: dict[str, Any] = {
                "user_id": user_id,
                "language": "ts",
                "native_language": "ru",
                "level": "A1",
                "status": "active",
            }
            values.update(bad)
            session.add(TutorConversation(**values))
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()


async def test_message_role_check(migrated_db: None, session_factory: Any) -> None:
    user_id = await prepare_user(session_factory, 9202)
    conversation_id = await create_conversation(session_factory, user_id)

    async with session_scope(session_factory) as session:
        session.add(
            TutorMessage(
                conversation_id=conversation_id,
                role="system",
                text="x",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_user_message_with_reply_link_rejected(
    migrated_db: None, session_factory: Any
) -> None:
    """CHECK role/link: a user message must not have in_reply_to."""
    user_id = await prepare_user(session_factory, 9203)
    conversation_id = await create_conversation(session_factory, user_id)

    async with session_scope(session_factory) as session:
        session.add(
            TutorMessage(
                conversation_id=conversation_id,
                role="user",
                text="x",
                in_reply_to_message_id=None,
            )
        )
        await session.flush()  # valid: user without link
        await session.rollback()

    async with session_scope(session_factory) as session:
        session.add(
            TutorMessage(
                conversation_id=conversation_id,
                role="user",
                text="x",
                in_reply_to_message_id=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_assistant_without_link_rejected(
    migrated_db: None, session_factory: Any
) -> None:
    """CHECK role/link: an assistant message requires in_reply_to."""
    user_id = await prepare_user(session_factory, 9204)
    conversation_id = await create_conversation(session_factory, user_id)

    async with session_scope(session_factory) as session:
        session.add(
            TutorMessage(
                conversation_id=conversation_id,
                role="assistant",
                text="x",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_assistant_reply_to_same_conversation_message_saved(
    migrated_db: None, session_factory: Any
) -> None:
    user_id = await prepare_user(session_factory, 9205)
    conversation_id = await create_conversation(session_factory, user_id)
    user_message_id = await add_message(
        session_factory, conversation_id, role="user", text="hello"
    )

    reply_id = await add_message(
        session_factory,
        conversation_id,
        role="assistant",
        text="hi there",
        in_reply_to_message_id=user_message_id,
    )
    assert reply_id is not None


async def test_reply_to_foreign_conversation_message_rejected(
    migrated_db: None, session_factory: Any
) -> None:
    """The composite FK must reject a reply pointing to a message of
    another conversation."""
    user_id = await prepare_user(session_factory, 9206)
    conversation_id = await create_conversation(session_factory, user_id)
    # A second conversation of the same user, closed (so the partial
    # unique index for active conversations does not interfere).
    foreign_conversation_id = await create_conversation(
        session_factory, user_id, status="closed"
    )
    foreign_message_id = await add_message(
        session_factory, foreign_conversation_id, role="user", text="x"
    )

    async with session_scope(session_factory) as session:
        session.add(
            TutorMessage(
                conversation_id=conversation_id,
                role="assistant",
                text="reply",
                in_reply_to_message_id=foreign_message_id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


# ---------------------------------------------------------------------------
# Partial unique indexes
# ---------------------------------------------------------------------------


async def test_only_one_active_conversation_per_user(
    migrated_db: None, session_factory: Any
) -> None:
    user_id = await prepare_user(session_factory, 9207)
    first = await create_conversation(session_factory, user_id)
    # A second active conversation for the same user violates the
    # partial unique index (session_scope rolls back on failure).
    with pytest.raises(IntegrityError):
        await create_conversation(session_factory, user_id)

    # Another user may have an active conversation.
    other_id = await prepare_user(session_factory, 9208)
    other = await create_conversation(session_factory, other_id)
    assert other is not None

    # After closing the first, a new active one is allowed.
    async with session_scope(session_factory) as session:
        conversation = await session.get(TutorConversation, first)
        assert conversation is not None
        conversation.status = "closed"
        conversation.closed_at = datetime.now(UTC)
    reopened = await create_conversation(session_factory, user_id)
    assert reopened is not None


async def test_client_request_id_unique_per_conversation(
    migrated_db: None, session_factory: Any
) -> None:
    user_id = await prepare_user(session_factory, 9209)
    conversation_id = await create_conversation(session_factory, user_id)
    await add_message(
        session_factory,
        conversation_id,
        role="user",
        text="one",
        client_request_id="key-1",
    )
    with pytest.raises(IntegrityError):
        await add_message(
            session_factory,
            conversation_id,
            role="user",
            text="two",
            client_request_id="key-1",
        )

    # The same key in another conversation is fine.
    second_conversation = await create_conversation(
        session_factory, user_id, status="closed"
    )
    key_ok = await add_message(
        session_factory,
        second_conversation,
        role="user",
        text="three",
        client_request_id="key-1",
    )
    assert key_ok is not None

    # Assistant messages are outside the partial index (which covers
    # user messages only): the same key on an assistant reply is fine.
    user_message_id = await add_message(
        session_factory,
        conversation_id,
        role="user",
        text="question",
        client_request_id="key-2",
    )
    assistant_ok = await add_message(
        session_factory,
        conversation_id,
        role="assistant",
        text="reply",
        client_request_id="key-1",
        in_reply_to_message_id=user_message_id,
    )
    assert assistant_ok is not None


# ---------------------------------------------------------------------------
# Repository ownership
# ---------------------------------------------------------------------------


async def test_ownership_of_conversations_and_messages(
    migrated_db: None, session_factory: Any
) -> None:
    owner_id = await prepare_user(session_factory, 9210)
    stranger_id = await prepare_user(session_factory, 9211)
    conversation_id = await create_conversation(session_factory, owner_id)
    await add_message(
        session_factory,
        conversation_id,
        role="user",
        text="mine",
        client_request_id="mine-key",
    )

    async with session_scope(session_factory) as session:
        conversation_repo = make_conversation_repo(session)
        message_repo = make_message_repo(session)

        # Owner sees it; stranger gets None (same as missing).
        assert (
            await conversation_repo.get_for_user(
                user_id=owner_id, conversation_id=conversation_id
            )
        ) is not None
        assert (
            await conversation_repo.get_for_user(
                user_id=stranger_id, conversation_id=conversation_id
            )
        ) is None

        # History is isolated.
        assert (
            len(
                await message_repo.list_for_conversation(
                    user_id=owner_id, conversation_id=conversation_id
                )
            )
            == 1
        )
        assert (
            await message_repo.list_for_conversation(
                user_id=stranger_id, conversation_id=conversation_id
            )
        ) == []

        # Idempotency lookups are user-scoped.
        assert (
            await message_repo.get_user_message_by_client_request_id(
                user_id=owner_id,
                conversation_id=conversation_id,
                client_request_id="mine-key",
            )
        ) is not None
        assert (
            await message_repo.get_user_message_by_client_request_id(
                user_id=stranger_id,
                conversation_id=conversation_id,
                client_request_id="mine-key",
            )
        ) is None

        # The assistant-reply lookup is user-scoped too.
        user_message = await message_repo.get_user_message_by_client_request_id(
            user_id=owner_id,
            conversation_id=conversation_id,
            client_request_id="mine-key",
        )
        assert user_message is not None
        await message_repo.add(
            conversation_id=conversation_id,
            role="assistant",
            text="reply",
            in_reply_to_message_id=user_message.id,
        )
        assert (
            await message_repo.get_assistant_reply(
                user_id=owner_id, in_reply_to_message_id=user_message.id
            )
        ) is not None
        assert (
            await message_repo.get_assistant_reply(
                user_id=stranger_id, in_reply_to_message_id=user_message.id
            )
        ) is None


async def test_list_for_user_and_get_active(
    migrated_db: None, session_factory: Any
) -> None:
    user_id = await prepare_user(session_factory, 9212)
    conversation_id = await create_conversation(session_factory, user_id)
    stranger_id = await prepare_user(session_factory, 9213)

    async with session_scope(session_factory) as session:
        conversation_repo = make_conversation_repo(session)
        active = await conversation_repo.get_active_for_user(user_id)
        assert active is not None and active.id == conversation_id
        assert await conversation_repo.get_active_for_user(stranger_id) is None
        listed = await conversation_repo.list_for_user(user_id)
        assert [conversation.id for conversation in listed] == [conversation_id]
        assert await conversation_repo.list_for_user(stranger_id) == []


async def test_second_fresh_claim_is_busy(
    migrated_db: None, session_factory: Any
) -> None:
    """Repository-level: only one claim can be held at a time."""
    async with session_scope(session_factory) as session:
        profile = await create_profile_row(session, telegram_id=9321)
    user_id = profile.user_id
    conversation_id = await create_conversation(session_factory, user_id)
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        first = await TutorConversationRepository(session).try_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=uuid.uuid4(),
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=now,
        )
        assert first == "claimed"
        second = await TutorConversationRepository(session).try_claim(
            user_id=user_id,
            conversation_id=conversation_id,
            claim_id=uuid.uuid4(),
            min_interval_seconds=0,
            claim_timeout_seconds=199,
            now=now,
        )
        assert second == "busy"
