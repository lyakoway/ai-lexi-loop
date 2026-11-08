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

    # LLM integration (Stage 6). Disabled by default: the product works
    # fully without it; enabling requires an explicit, valid configuration.
    llm_enabled: bool = False
    llm_provider: Literal["openai_compatible"] = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0
    llm_retry_attempts: int = 2
    llm_max_output_tokens: int = 1024

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalized

    @field_validator("llm_timeout_seconds")
    @classmethod
    def _validate_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("llm_retry_attempts")
    @classmethod
    def _validate_not_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must not be negative")
        return value

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Fail fast in production when a required secret is missing."""
        if (
            self.environment == "production"
            and not self.telegram_bot_token.get_secret_value()
        ):
            raise ValueError("TELEGRAM_BOT_TOKEN is required in production")
        return self

    @model_validator(mode="after")
    def _validate_llm_settings(self) -> "Settings":
        """An enabled LLM integration must be fully configured.

        No silent fallback to a default endpoint or model: a broken
        configuration surfaces at startup, not at first call time.
        """
        if self.llm_enabled:
            missing = [
                name
                for name, value in (
                    ("LLM_BASE_URL", self.llm_base_url),
                    ("LLM_MODEL", self.llm_model),
                    ("LLM_API_KEY", self.llm_api_key.get_secret_value()),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"LLM_ENABLED=true requires {', '.join(missing)} to be set"
                )
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
