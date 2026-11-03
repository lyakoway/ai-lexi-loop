"""Health-check endpoints.

``/health`` answers liveness: is the process able to serve requests at all.
``/ready`` answers readiness: are the dependencies the product needs
(database, Telegram polling) actually working. Kubernetes-style probes and
Docker healthchecks rely on this distinction, so a broken database must
turn ``/ready`` red instead of taking the process down.
"""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.bot.runner import BotRunner

logger = logging.getLogger(__name__)

router = APIRouter()

READINESS_DB_TIMEOUT_SECONDS = 3.0


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: the process is up and serving requests."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness probe: report the state of every runtime dependency."""
    database_status = await _check_database(request)
    bot_status = _bot_status(request)

    components = {"database": database_status, "bot": bot_status}
    ready = database_status == "ok" and bot_status != "failed"
    body = {"status": "ready" if ready else "unavailable", **components}
    return JSONResponse(status_code=200 if ready else 503, content=body)


async def _check_database(request: Request) -> str:
    """Run a trivial query with a timeout; map any failure to ``down``."""
    engine = request.app.state.engine
    try:
        async with asyncio.timeout(READINESS_DB_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except TimeoutError:
        logger.warning("readiness check: database timed out")
        return "down"
    except Exception:
        logger.warning("readiness check: database unavailable", exc_info=True)
        return "down"
    return "ok"


def _bot_status(request: Request) -> str:
    """Report the supervised polling status, or ``disabled``."""
    runner: BotRunner | None = getattr(request.app.state, "bot_runner", None)
    if runner is None:
        return "disabled"
    return runner.status
