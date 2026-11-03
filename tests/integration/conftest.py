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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.session import SessionFactory, session_scope
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


@pytest.fixture
async def db_session(
    migrated_db: None, session_factory: SessionFactory
) -> AsyncIterator[AsyncSession]:
    """A clean session with an empty users table."""
    async with session_scope(session_factory) as session:
        await session.execute(text("TRUNCATE users RESTART IDENTITY"))
        yield session
