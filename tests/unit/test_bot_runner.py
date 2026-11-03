"""Tests for the supervised polling runner."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.bot.runner import BotRunner, BotStatus


async def _poll_forever(*args: Any, **kwargs: Any) -> None:
    """Stand-in for a long-running polling loop (mock passes the bot)."""
    await asyncio.sleep(60)


async def _poll_and_crash(*args: Any, **kwargs: Any) -> None:
    """Stand-in for a polling loop that fails immediately."""
    raise RuntimeError("api error")


def make_runner(
    polling_behavior: Any,
) -> tuple[BotRunner, AsyncMock, MagicMock]:
    """Build a runner around mocks for bot, dispatcher and polling."""
    bot = MagicMock()
    bot.session.close = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.start_polling = AsyncMock(side_effect=polling_behavior)
    runner = BotRunner(bot=bot, dispatcher=dispatcher)
    return runner, dispatcher, bot


async def _wait_for_status(runner: BotRunner, expected: BotStatus) -> None:
    """Wait until the runner reports the expected status."""
    for _ in range(500):
        if runner.status == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"runner never reached status {expected!r}")


async def test_start_runs_polling_until_stopped() -> None:
    runner, dispatcher, _ = make_runner(_poll_forever)

    status_before_start = runner.status
    await runner.start()
    await _wait_for_status(runner, "running")
    status_while_running = runner.status
    await runner.stop()
    status_after_stop = runner.status
    assert (
        status_before_start,
        status_while_running,
        status_after_stop,
    ) == ("stopped", "running", "stopped")
    dispatcher.start_polling.assert_awaited_once()


async def test_polling_failure_marks_runner_failed() -> None:
    """A crashed polling task must be visible in the status."""
    runner, _, _ = make_runner(_poll_and_crash)

    await runner.start()
    await _wait_for_status(runner, "failed")

    await runner.stop()
    # A failed runner stays failed so /ready keeps reporting it.
    assert runner.status == "failed"


async def test_stop_is_idempotent() -> None:
    runner, _, bot = make_runner(_poll_forever)

    await runner.start()
    await runner.stop()
    await runner.stop()

    bot.session.close.assert_awaited_once()


async def test_start_twice_is_rejected() -> None:
    runner, _, _ = make_runner(_poll_forever)

    await runner.start()
    try:
        await runner.start()
        raise AssertionError("second start must be rejected")
    except RuntimeError:
        pass
    finally:
        await runner.stop()
