"""FastAPI application factory and process lifecycle.

The HTTP API and the Telegram bot run in one process. The lifespan owns
every long-lived resource: the database engine, the session factory and
the supervised bot polling task. Each of them is closed on shutdown,
and a polling crash is visible through ``/ready`` (see
:class:`~app.bot.runner.BotRunner`).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.middleware import ObservabilityMiddleware
from app.api.routes import health, v1
from app.bot.builder import create_bot, create_dispatcher
from app.bot.runner import BotRunner
from app.core.config import Settings, get_settings
from app.core.metrics import render_prometheus
from app.db.session import (
    SessionFactory,
    build_engine,
    build_session_factory,
)
from app.integrations.llm import build_llm_client
from app.integrations.llm.client import StructuredClient as StructuredClientType
from app.integrations.llm.recorder import LlmCallRecorder
from app.repositories import StorageError

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
    app.add_middleware(ObservabilityMiddleware)
    app.include_router(health.router)
    app.include_router(v1.router)
    app.add_exception_handler(Exception, _unhandled_error_handler)
    app.add_exception_handler(StorageError, _storage_error_handler)
    app.get("/metrics", include_in_schema=False)(_metrics_endpoint)
    return app


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected errors with full detail, answer without internals."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


async def _storage_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Database failures surface as 503, never as silent success.

    Registered for :class:`StorageError`; the wider signature satisfies
    Starlette's exception-handler protocol.
    """
    assert isinstance(exc, StorageError)
    logger.error("storage error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=503, content={"detail": "Service temporarily unavailable"}
    )


async def _metrics_endpoint() -> PlainTextResponse:
    """Prometheus text exposition of aggregate counters."""
    return PlainTextResponse(
        render_prometheus(), media_type="text/plain; version=0.0.4"
    )


@asynccontextmanager
async def _lifespan(app: FastAPI, settings: Settings) -> AsyncIterator[None]:
    engine = build_engine(settings)
    session_factory: SessionFactory = build_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory

    llm_client: StructuredClientType | None = None
    llm_http: httpx.AsyncClient | None = None
    if settings.llm_enabled:
        llm_http = httpx.AsyncClient()
        llm_client = build_llm_client(
            settings, llm_http, LlmCallRecorder(session_factory)
        )
        logger.info(
            "llm integration enabled (provider=%s, model=%s)",
            settings.llm_provider,
            settings.llm_model,
        )
    else:
        logger.info("llm integration disabled")
    app.state.llm_client = llm_client

    bot_runner: BotRunner | None = None
    if settings.bot_enabled:
        bot_runner = BotRunner(
            create_bot(settings), create_dispatcher(session_factory, llm_client)
        )
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
        if llm_http is not None:
            await llm_http.aclose()
        app.state.llm_client = None
        await engine.dispose()
        app.state.engine = None
        logger.info("application stopped")
