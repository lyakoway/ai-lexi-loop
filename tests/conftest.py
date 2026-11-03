"""Shared test helpers."""

from typing import Any

from app.core.config import Settings

# A port that is closed on every machine; used to simulate an unavailable
# database without waiting for a timeout.
UNREACHABLE_DATABASE_URL = "postgresql://user:pass@127.0.0.1:1/unreachable"


def make_settings(**overrides: Any) -> Settings:
    """Build isolated test settings.

    ``_env_file=None`` guarantees a developer's ``.env`` never leaks into
    tests. Defaults describe a harmless test environment.
    """
    defaults: dict[str, Any] = {
        "database_url": "postgresql://user:pass@localhost:5432/ai_lexi_loop_test",
        "testing": True,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)
