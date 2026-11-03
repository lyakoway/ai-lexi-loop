"""Application settings loaded from environment variables and ``.env``.

All configuration is resolved in one place. Secrets (the Telegram bot token)
are stored as :class:`pydantic.SecretStr` so they never leak into logs or
repr output.
"""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "staging", "production"]


class Settings(BaseSettings):
    """Central application configuration.

    Values are read from environment variables (case-insensitive) and,
    for local development, from a ``.env`` file in the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "dev"
    database_url: PostgresDsn
    telegram_bot_token: SecretStr = SecretStr("")
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    testing: bool = False

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalized

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Fail fast in production when a required secret is missing."""
        if (
            self.environment == "production"
            and not self.telegram_bot_token.get_secret_value()
        ):
            raise ValueError("TELEGRAM_BOT_TOKEN is required in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def bot_enabled(self) -> bool:
        """Whether Telegram polling should start.

        The token alone is not enough in test mode: tests must never touch
        the Telegram API.
        """
        return bool(self.telegram_bot_token.get_secret_value()) and not self.testing

    @property
    def sqlalchemy_url(self) -> str:
        """Database URL in SQLAlchemy async form.

        Accepts both ``postgresql://`` and ``postgresql+asyncpg://`` input and
        always yields the asyncpg variant required by the async engine.
        """
        url = str(self.database_url)
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings.

    The cache makes repeated access cheap inside a single process. Tests
    construct ``Settings`` directly instead of using this function, so they
    are never affected by the cache.
    """
    return Settings()
