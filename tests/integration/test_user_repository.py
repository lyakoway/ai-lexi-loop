"""Integration tests for user persistence on a real PostgreSQL."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import SessionFactory, session_scope
from app.repositories import StorageError, UserRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


async def test_migration_creates_users_table(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """The Alembic migration must produce the expected table shape."""
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT column_name, is_nullable FROM "
                "information_schema.columns WHERE table_name = 'users'"
            )
        )
        columns = {row[0]: row[1] for row in rows}
    assert columns == {
        "id": "NO",
        "telegram_user_id": "NO",
        "created_at": "NO",
        "updated_at": "NO",
    }


async def test_get_or_create_creates_then_reuses(
    db_session: AsyncSession,
) -> None:
    repository = UserRepository(db_session)

    created_user, created = await repository.get_or_create(777)
    assert created is True
    assert created_user.id is not None
    assert created_user.created_at is not None

    same_user, created_again = await repository.get_or_create(777)
    assert created_again is False
    assert same_user.id == created_user.id


async def test_telegram_user_id_is_unique(db_session: AsyncSession) -> None:
    """A second user with the same Telegram ID must be rejected by the DB."""
    repository = UserRepository(db_session)
    await repository.create(999)

    with pytest.raises(StorageError) as exc_info:
        await repository.create(999)

    assert exc_info.value.conflict is True
    # After a failed flush the transaction can only be rolled back; do it
    # here so the fixture's commit-on-exit stays valid.
    await db_session.rollback()


async def test_separate_sessions_see_persisted_users(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """A committed user must be visible from a new session."""
    async with session_scope(session_factory) as session:
        await UserRepository(session).create(555)

    async with session_scope(session_factory) as session:
        user = await UserRepository(session).get_by_telegram_id(555)
        assert user is not None
        assert isinstance(user, User)
        assert user.telegram_user_id == 555
