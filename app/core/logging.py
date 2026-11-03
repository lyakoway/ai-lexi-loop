"""Logging configuration.

A single ``dictConfig`` for the whole process: application loggers, uvicorn
and alembic all share one console format. Secrets are never logged because
``Settings`` keeps them as ``SecretStr``.
"""

import logging
import logging.config

from app.core.config import Settings

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(settings: Settings) -> None:
    """Configure process-wide logging from application settings."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {"format": _LOG_FORMAT},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "console",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": settings.log_level,
            },
            "loggers": {
                "uvicorn": {"level": settings.log_level, "propagate": True},
                "uvicorn.error": {"level": settings.log_level, "propagate": True},
                "uvicorn.access": {"level": settings.log_level, "propagate": True},
                "alembic": {"level": settings.log_level, "propagate": True},
                "aiogram": {"level": settings.log_level, "propagate": True},
                "asyncpg": {"level": "WARNING", "propagate": True},
            },
        }
    )
