"""Fixtures for integration tests.

The database schema is managed by Alembic: the session fixture upgrades to
head once, tests work inside transactions/with truncation, and the schema
is dropped at the end of the session.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.models import Expression, Profile, User
from app.db.seed import seed_expressions
from app.db.session import SessionFactory, session_scope
from app.repositories import (
    AnalyticsRepository,
    ExpressionProgressRepository,
    ExpressionRepository,
    ProfileRepository,
    UserRepository,
)
from app.services.analytics import AnalyticsService
from app.services.lessons import LessonService
from app.services.profiles import ProfileService
from app.services.reviews import ReviewService
from tests.conftest import make_settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[None]:
    """Apply all migrations before the session, drop schema after."""
    old_url = os.environ.get("DATABASE_URL")
    # migrations/env.py builds Settings() from the environment; point it at
    # the test database for the duration of the run.
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    config = _alembic_config()
    try:
        command.upgrade(config, "head")
        yield
        command.downgrade(config, "base")
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return make_settings(database_url=TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def test_engine(test_settings: Settings) -> Iterator[AsyncEngine]:
    # NullPool: each session checks out a fresh connection on the current
    # test's event loop, avoiding cross-loop connections between tests.
    engine = create_async_engine(test_settings.sqlalchemy_url, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture(scope="session")
def session_factory(test_engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(test_engine, expire_on_commit=False)


async def reset_database(session: AsyncSession) -> None:
    """Truncate every user-bound table and re-seed the dictionary.

    Ids restart at 1, so events, tokens or progress of previous tests
    never leak into the next one.
    """
    await session.execute(
        text(
            "TRUNCATE analytics_events, api_tokens, user_identities, "
            "expression_progress, profiles, users, expressions "
            "RESTART IDENTITY CASCADE"
        )
    )
    await seed_expressions(session)


@pytest.fixture
async def db_session(
    migrated_db: None, session_factory: SessionFactory
) -> AsyncIterator[AsyncSession]:
    """A session over a pristine database (see ``reset_database``)."""
    async with session_scope(session_factory) as session:
        await reset_database(session)
        yield session


def make_analytics(session: AsyncSession) -> AnalyticsService:
    """Analytics service bound to the given session."""
    return AnalyticsService(AnalyticsRepository(session))


async def insert_test_expression(
    session: AsyncSession,
    *,
    text_value: str,
    level: str = "A1",
    topic: str = "everyday",
) -> Expression:
    """Insert a dictionary expression under the test language 'ts'."""
    expression = Expression(
        language="ts",
        text=text_value,
        translation="перевод",
        ipa="aɪ piː eɪ",
        phonetic_hint_ru="пОдсказка",
        example="example",
        example_translation="пример",
        level=level,
        topic=topic,
    )
    session.add(expression)
    await session.flush()
    return expression


async def create_profile_row(
    session: AsyncSession,
    *,
    telegram_id: int = 9001,
    new_items_per_day: int = 5,
    level: str = "A1",
    topics: list[str] | None = None,
) -> Profile:
    """Create a user (via identity) with a profile; returns the profile."""
    user, _ = await UserRepository(session).get_or_create_by_identity(
        "telegram", str(telegram_id)
    )
    payload: dict[str, object] = {
        "native_language": "ru",
        "target_language": "ts",
        "level": level,
        "learning_goal": "work",
        "topics": topics if topics is not None else ["everyday"],
        "preferred_accent": "uk",
        "daily_study_minutes": 15,
        "new_items_per_day": new_items_per_day,
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "UTC",
    }
    from app.schemas.profile import ProfileCreate

    await ProfileService(
        ProfileRepository(session), make_analytics(session)
    ).save_profile(user.id, ProfileCreate.model_validate(payload))
    result = await session.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one()
    assert profile is not None
    return profile


def make_lesson_service(session: AsyncSession) -> LessonService:
    """LessonService wired for integration tests."""
    return LessonService(
        ExpressionRepository(session),
        ExpressionProgressRepository(session),
        make_analytics(session),
    )


def make_review_service(session: AsyncSession) -> ReviewService:
    """ReviewService wired for integration tests."""
    return ReviewService(
        ExpressionProgressRepository(session),
        ExpressionRepository(session),
        make_analytics(session),
    )


async def create_user(session: AsyncSession, telegram_id: int = 9001) -> User:
    """Create (or reuse) an internal user bound to a Telegram identity."""
    user, _ = await UserRepository(session).get_or_create_by_identity(
        "telegram", str(telegram_id)
    )
    return user
