"""Tests for application settings."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from tests.conftest import make_settings


def test_settings_load_from_env_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings must be read from environment variables."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    settings = Settings(_env_file=None)
    assert settings.environment == "staging"
    assert str(settings.database_url) == "postgresql://u:p@localhost:5432/db"


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without DATABASE_URL the app must refuse to start."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_sqlalchemy_url_adds_asyncpg_driver() -> None:
    """Plain postgres:// URLs are transparently upgraded to asyncpg."""
    settings = make_settings(database_url="postgresql://u:p@localhost:5432/db")
    assert settings.sqlalchemy_url == "postgresql+asyncpg://u:p@localhost:5432/db"
    settings = make_settings(database_url="postgresql+asyncpg://u:p@localhost:5432/db")
    assert settings.sqlalchemy_url == "postgresql+asyncpg://u:p@localhost:5432/db"


@pytest.mark.parametrize(
    ("token", "testing", "expected"),
    [
        ("", True, False),
        ("", False, False),
        ("123:abc", True, False),
        ("123:abc", False, True),
    ],
)
def test_bot_enabled_requires_token_and_not_testing(
    token: str, testing: bool, expected: bool
) -> None:
    settings = make_settings(telegram_bot_token=token, testing=testing)
    assert settings.bot_enabled is expected


def test_production_requires_telegram_token() -> None:
    """Production must not start without a bot token."""
    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TOKEN"):
        make_settings(environment="production", telegram_bot_token="")


def test_production_accepts_valid_configuration() -> None:
    settings = make_settings(
        environment="production", telegram_bot_token="123:abc", testing=False
    )
    assert settings.is_production
    assert settings.bot_enabled


def test_invalid_environment_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(environment="on_prem")


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("debug", "DEBUG"), ("INFO", "INFO"), ("warning", "WARNING")],
)
def test_log_level_is_normalized(raw: str, normalized: str) -> None:
    settings = make_settings(log_level=raw)
    assert settings.log_level == normalized


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError, match="log_level"):
        make_settings(log_level="VERBOSE")


def test_telegram_token_is_masked_in_repr() -> None:
    """The token must never appear in repr or str of settings."""
    settings = make_settings(telegram_bot_token="123:super-secret")
    dumped = repr(settings) + str(settings.model_dump())
    assert "super-secret" not in dumped


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls return the same instance within a process."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()


def test_extra_environment_variables_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrelated env vars (e.g. from CI) must not break settings loading."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "42")
    settings: Any = Settings(_env_file=None)
    assert settings.testing is False
