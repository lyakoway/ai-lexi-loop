"""Supervised Telegram polling.

The bot and the HTTP API share one process. To avoid the failure mode where
polling silently dies while the process keeps looking healthy, polling runs
in a named task that records its own outcome; the readiness endpoint reads
that outcome via :attr:`BotRunner.status`.
"""

import asyncio
import contextlib
import logging
from types import TracebackType
from typing import Literal

from aiogram import Bot, Dispatcher

BotStatus = Literal["stopped", "starting", "running", "failed"]

logger = logging.getLogger(__name__)


class BotRunner:
    """Owns the Telegram polling task lifecycle."""

    def __init__(self, bot: Bot, dispatcher: Dispatcher) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._task: asyncio.Task[None] | None = None
        self._status: BotStatus = "stopped"

    @property
    def status(self) -> BotStatus:
        """Current polling status, surfaced through ``/ready``."""
        return self._status

    async def start(self) -> None:
        """Start polling in a background task."""
        if self._task is not None:
            raise RuntimeError("bot runner already started")
        self._status = "starting"
        self._task = asyncio.create_task(
            self._poll_forever(), name="telegram-bot-polling"
        )

    async def stop(self) -> None:
        """Cancel polling and close the bot session. Safe to call twice."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        if self._status != "failed":
            self._status = "stopped"
        await self._bot.session.close()

    async def _poll_forever(self) -> None:
        self._status = "running"
        try:
            await self._dispatcher.start_polling(self._bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._status = "failed"
            logger.exception("telegram polling stopped with an unexpected error")

    async def __aenter__(self) -> "BotRunner":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()
