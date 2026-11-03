"""FastAPI application factory and process lifecycle.

The HTTP API and the Telegram bot run in one process. The lifespan below
owns every long-lived resource: the database engine, the session factory
and the supervised bot polling task. Each of them is closed on shutdown,
and a polling crash is visible through ``/ready`` (see
:class:`~app.bot.runner.BotRunner`).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import health
from app.bot.builder import create_bot, create_dispatcher
from app.bot.runner import BotRunner
from app.core.config import Settings, get_settings
from app.db.session import build_engine, build_session_factory

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    ``settings`` may be injected by tests; production code uses the cached
    :func:`~app.core.config.get_settings`.
    """
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="ai-lexi-loop",
        lifespan=lambda app_: _lifespan(app_, settings),
    )
    app.include_router(health.router)
    app.add_exception_handler(Exception, _unhandled_error_handler)
    return app


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected errors with full detail, answer without internals."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@asynccontextmanager
async def _lifespan(app: FastAPI, settings: Settings) -> AsyncIterator[None]:
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    app.state.engine = engine

    bot_runner: BotRunner | None = None
    if settings.bot_enabled:
        bot_runner = BotRunner(create_bot(settings), create_dispatcher(session_factory))
        await bot_runner.start()
    else:
        logger.info(
            "telegram bot disabled (environment=%s, testing=%s)",
            settings.environment,
            settings.testing,
        )
    app.state.bot_runner = bot_runner

    logger.info(
        "application ready (environment=%s, port=%s)",
        settings.environment,
        settings.port,
    )
    try:
        yield
    finally:
        if bot_runner is not None:
            await bot_runner.stop()
        await engine.dispose()
        app.state.engine = None
        logger.info("application stopped")
