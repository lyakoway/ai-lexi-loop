"""Integration tests for the identity model on a real PostgreSQL.

Covers the Stage 5 identity requirements: existing Telegram users keep
one account, an external identity cannot map to two accounts, and
profiles/progress stay bound to the internal user_id.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory
from app.repositories import ProfileRepository, StorageError, UserRepository
from app.schemas.profile import ProfileCreate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


def profile_payload() -> dict[str, object]:
    return {
        "native_language": "ru",
        "target_language": "en",
        "level": "A1",
        "learning_goal": "work",
        "topics": ["work"],
        "preferred_accent": "uk",
        "daily_study_minutes": 15,
        "new_items_per_day": 5,
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "Europe/Moscow",
    }


async def test_users_table_has_no_external_ids(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """The internal account carries no channel identifiers anymore."""
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users'"
            )
        )
        columns = {row[0] for row in rows}
    assert columns == {"id", "created_at", "updated_at"}


async def test_identity_table_enforces_unique_pairs(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'user_identities'"
            )
        )
        columns = {row[0] for row in rows}
        constraints = await session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'user_identities'::regclass"
            )
        )
        names = {row[0] for row in constraints}
    assert {
        "id",
        "user_id",
        "provider",
        "provider_user_id",
        "created_at",
        "updated_at",
    } == columns
    assert "uq_user_identities_provider_provider_user_id" in names


async def test_one_telegram_identity_one_account(db_session: AsyncSession) -> None:
    """Repeated registration returns the same internal user."""
    repository = UserRepository(db_session)

    first, created_first = await repository.get_or_create_by_identity(
        "telegram", "4242"
    )
    second, created_second = await repository.get_or_create_by_identity(
        "telegram", "4242"
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


async def test_same_provider_id_for_two_users_conflicts(
    db_session: AsyncSession,
) -> None:
    """The same Telegram ID can never bind to a second account."""
    repository = UserRepository(db_session)
    first, _ = await repository.get_or_create_by_identity("telegram", "1111")
    second, _ = await repository.get_or_create_by_identity("telegram", "2222")

    with pytest.raises(StorageError) as exc_info:
        await repository.link_identity(second.id, "telegram", "1111")

    assert exc_info.value.conflict is True
    # The conflict was detected before any write; the first binding
    # stays intact without a rollback.
    found = await repository.get_by_identity("telegram", "1111")
    assert found is not None and found[0].id == first.id


async def test_profile_and_progress_follow_internal_user(
    db_session: AsyncSession,
) -> None:
    """User data stays reachable through the internal user_id."""
    repository = UserRepository(db_session)
    user, _ = await repository.get_or_create_by_identity("telegram", "3333")
    await ProfileRepository(db_session).create(
        ProfileCreate.model_validate(profile_payload()), user.id
    )

    found = await repository.get_by_identity("telegram", "3333")
    assert found is not None
    resolved_user, _identity = found
    profiles = await ProfileRepository(db_session).get_by_user_id(resolved_user.id)
    assert profiles is not None
    assert profiles.timezone == "Europe/Moscow"


async def test_multiple_identities_one_user(db_session: AsyncSession) -> None:
    """One internal account can own several external identities."""
    repository = UserRepository(db_session)
    user, _ = await repository.get_or_create_by_identity("telegram", "4444")

    linked = await repository.link_identity(user.id, "email", "user@example.com")

    assert linked.provider == "email"
    found = await repository.get_by_identity("email", "user@example.com")
    assert found is not None and found[0].id == user.id
