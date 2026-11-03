"""Async engine and session management.

Factories are plain functions so callers own the lifecycle: the FastAPI
lifespan creates an engine at startup and disposes it at shutdown. Nothing
here creates a module-level engine or session, which keeps tests in control.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

SessionFactory = async_sessionmaker[AsyncSession]


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine for the configured PostgreSQL database."""
    return create_async_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
    )


def build_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(session_factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Provide a transactional session scope.

    Commits on success, rolls back on any exception, and always closes the
    session.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
