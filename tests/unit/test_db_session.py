"""Tests for session lifecycle and repository error translation."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.session import SessionFactory, session_scope
from app.repositories import StorageError, UserRepository


class FakeAsyncSession:
    """Minimal stand-in for AsyncSession recording transaction calls."""

    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.closed = False

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.closed = True


class FakeSessionFactory:
    """Mimics async_sessionmaker: returns a context manager per call."""

    def __init__(self, session: FakeAsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_SessionContext":
        return _SessionContext(self._session)


class _SessionContext:
    def __init__(self, session: FakeAsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeAsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        await self._session.close()


def fake_session_factory(session: FakeAsyncSession) -> SessionFactory:
    return FakeSessionFactory(session)  # type: ignore[return-value]


async def test_session_scope_commits_on_success() -> None:
    session = FakeAsyncSession()

    async with session_scope(fake_session_factory(session)):
        pass

    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert session.closed


async def test_session_scope_rolls_back_on_error() -> None:
    session = FakeAsyncSession()

    with pytest.raises(RuntimeError, match="handler failed"):
        async with session_scope(fake_session_factory(session)):
            raise RuntimeError("handler failed")

    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert session.closed


def make_mock_session(
    execute_side_effect: BaseException | None = None,
    flush_side_effect: BaseException | None = None,
) -> Any:
    """A session mock with sync ``add`` and async ``execute``/``flush``."""
    session = MagicMock()
    session.add = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    )
    session.flush = AsyncMock(side_effect=flush_side_effect)
    return session


def make_integrity_error() -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception("duplicate key"))


async def test_get_or_create_reports_conflict() -> None:
    """A duplicate telegram_user_id must surface as a conflict error."""
    session = make_mock_session(flush_side_effect=make_integrity_error())

    repository = UserRepository(session)

    with pytest.raises(StorageError) as exc_info:
        await repository.get_or_create(telegram_user_id=42)

    assert exc_info.value.conflict is True


async def test_storage_errors_hide_sqlalchemy_details() -> None:
    """Generic database failures become StorageError with the cause kept."""
    session = make_mock_session(
        execute_side_effect=OperationalError(
            "SELECT ...", {}, Exception("connection refused")
        )
    )

    repository = UserRepository(session)

    with pytest.raises(StorageError) as exc_info:
        await repository.get_by_telegram_id(telegram_user_id=42)

    assert exc_info.value.conflict is False
    assert isinstance(exc_info.value.__cause__, OperationalError)
