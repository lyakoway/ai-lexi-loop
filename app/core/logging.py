"""Logging configuration.

A single ``dictConfig`` for the whole process: application loggers, uvicorn
and alembic share one console format. In production records are rendered
as one-line JSON (queryable, no extra dependency); everywhere else the
format stays human-readable. The current request id is attached to every
record via the context variable set by the HTTP middleware. Secrets are
never logged because ``Settings`` keeps them as ``SecretStr``.
"""

import json
import logging
import logging.config

from app.core.config import Settings
from app.core.context import current_request_id

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"


class RequestIdFilter(logging.Filter):
    """Attach the in-flight request id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line with the standard fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": current_request_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(settings: Settings) -> None:
    """Configure process-wide logging from application settings."""
    formatter = "json" if settings.is_production else "console"
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_id": {"()": RequestIdFilter},
            },
            "formatters": {
                "console": {"format": _LOG_FORMAT},
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter,
                    "filters": ["request_id"],
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
