"""Integration tests for profile persistence on a real PostgreSQL."""

import os
from datetime import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, session_scope
from app.repositories import ProfileRepository, StorageError, UserRepository
from app.schemas.profile import ProfileCreate, ProfileUpdate
from tests.integration.conftest import make_analytics

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


def make_create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "native_language": "ru",
        "target_language": "en",
        "level": "B1",
        "learning_goal": "work",
        "topics": ["work", "technology"],
        "preferred_accent": "uk",
        "daily_study_minutes": 15,
        "new_items_per_day": 5,
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "Europe/Moscow",
    }
    payload.update(overrides)
    return payload


async def create_test_user(db_session: AsyncSession) -> int:
    user, _ = await UserRepository(db_session).get_or_create_by_identity(
        "telegram", "4242"
    )
    return user.id


async def test_migration_creates_profiles_table(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """The Alembic migration must produce the expected columns."""
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'profiles'"
            )
        )
        columns = {row[0] for row in rows}
    assert {
        "id",
        "user_id",
        "native_language",
        "target_language",
        "level",
        "learning_goal",
        "topics",
        "preferred_accent",
        "daily_study_minutes",
        "new_items_per_day",
        "lesson_time",
        "review_time",
        "timezone",
        "created_at",
        "updated_at",
    }.issubset(columns)


async def test_create_and_read_back_profile(db_session: AsyncSession) -> None:
    """Saved settings survive a fresh read from the database."""
    user_id = await create_test_user(db_session)
    repository = ProfileRepository(db_session)

    await repository.create(
        ProfileCreate.model_validate(make_create_payload()), user_id
    )

    loaded = await repository.get_by_user_id(user_id)
    assert loaded is not None
    assert loaded.level == "B1"
    assert loaded.topics == ["work", "technology"]
    assert loaded.lesson_time == time(8, 0)
    assert loaded.timezone == "Europe/Moscow"
    assert loaded.native_language == "ru"
    assert loaded.learning_goal == "work"


async def test_profile_user_id_is_unique(db_session: AsyncSession) -> None:
    """A second profile for the same user must be rejected."""
    user_id = await create_test_user(db_session)
    repository = ProfileRepository(db_session)
    data = ProfileCreate.model_validate(make_create_payload())
    await repository.create(data, user_id)

    with pytest.raises(StorageError) as exc_info:
        await repository.create(data, user_id)

    assert exc_info.value.conflict is True
    await db_session.rollback()


async def test_partial_update_touches_only_given_fields(
    db_session: AsyncSession,
) -> None:
    """Changing one setting must not lose the others."""
    user_id = await create_test_user(db_session)
    repository = ProfileRepository(db_session)
    profile = await repository.create(
        ProfileCreate.model_validate(make_create_payload()), user_id
    )

    await repository.apply_update(
        profile, ProfileUpdate.model_validate({"level": "C1"})
    )

    reloaded = await repository.get_by_user_id(user_id)
    assert reloaded is not None
    assert reloaded.level == "C1"
    assert reloaded.topics == ["work", "technology"]
    assert reloaded.daily_study_minutes == 15
    assert reloaded.timezone == "Europe/Moscow"
    assert reloaded.lesson_time == time(8, 0)


async def test_update_rejects_invalid_level_in_database(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """The CHECK constraint must back up application-level validation.

    The flush below bypasses the repository (and its error translation),
    so the raw SQLAlchemy error surfaces here.
    """
    from sqlalchemy.exc import IntegrityError

    async with session_scope(session_factory) as session:
        user, _ = await UserRepository(session).get_or_create_by_identity(
            "telegram", "777"
        )
        profile = await ProfileRepository(session).create(
            ProfileCreate.model_validate(make_create_payload()), user.id
        )
        profile.level = "X9"
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_upsert_replaces_profile_keeping_row(
    db_session: AsyncSession,
) -> None:
    """Re-running onboarding keeps one row and overwrites fields."""
    from app.services import ProfileService

    user_id = await create_test_user(db_session)
    service = ProfileService(ProfileRepository(db_session), make_analytics(db_session))

    first = await service.save_profile(
        user_id, ProfileCreate.model_validate(make_create_payload())
    )
    second = await service.save_profile(
        user_id,
        ProfileCreate.model_validate(
            make_create_payload(level="A1", topics=["sport"], new_items_per_day=10)
        ),
    )

    assert second.id == first.id
    assert second.created_at == first.created_at
    assert second.level == "A1"
    assert second.topics == ["sport"]
    assert second.new_items_per_day == 10

    rows = (await db_session.execute(text("SELECT id FROM profiles"))).fetchall()
    assert len(rows) == 1


async def test_update_profile_validates_merged_state(
    db_session: AsyncSession,
) -> None:
    """A patch making native equal target must be rejected."""
    import pydantic

    from app.services import ProfileService

    user_id = await create_test_user(db_session)
    service = ProfileService(ProfileRepository(db_session), make_analytics(db_session))
    await service.save_profile(
        user_id, ProfileCreate.model_validate(make_create_payload())
    )

    with pytest.raises(pydantic.ValidationError):
        await service.update_profile(
            user_id, ProfileUpdate.model_validate({"native_language": "en"})
        )


async def test_update_profile_requires_existing_profile(
    db_session: AsyncSession,
) -> None:
    from app.services import ProfileNotConfiguredError, ProfileService

    service = ProfileService(ProfileRepository(db_session), make_analytics(db_session))
    with pytest.raises(ProfileNotConfiguredError):
        await service.update_profile(
            999999, ProfileUpdate.model_validate({"level": "A1"})
        )
