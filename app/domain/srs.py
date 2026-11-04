"""Spaced repetition scheduling: a simplified SM-2 with binary answers.

The card state consists of an ease factor, the current interval and the
number of successful reviews. Two answers exist:

- «Помню» (remembered): ``review_count`` grows by one and the interval
  follows the classic SM-2 ladder — 1 day, 6 days, then
  ``round(interval * ease)`` days.
- «Забыл» (forgot): the card is reset — ``review_count`` back to zero,
  interval back to one day, ease factor multiplied by 0.8 (floor 1.3) so
  hard cards come back more often.

These constants are pragmatic defaults for an MVP, not scientifically
optimal intervals: predictability and testability matter more here, and
the values are easy to tune later. All computation happens in pure
functions over an injected timezone-aware "now", so the algorithm is
deterministic and independent of the database and Telegram.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

INITIAL_EASE_FACTOR = 2.5
MIN_EASE_FACTOR = 1.3
FIRST_INTERVAL_DAYS = 1
SECOND_INTERVAL_DAYS = 6
FORGOT_EASE_MULTIPLIER = 0.8
FORGOT_INTERVAL_DAYS = 1


def utc_now() -> datetime:
    """Current timezone-aware UTC time (single clock for services)."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class SrsState:
    """Scheduling state of one card before an answer."""

    ease_factor: float = INITIAL_EASE_FACTOR
    interval_days: int = FIRST_INTERVAL_DAYS
    review_count: int = 0


@dataclass(frozen=True)
class SrsUpdate:
    """New scheduling state of a card after an answer."""

    ease_factor: float
    interval_days: int
    review_count: int
    answered_at: datetime
    next_review_at: datetime


def apply_answer(state: SrsState, *, remembered: bool, now: datetime) -> SrsUpdate:
    """Compute the next scheduling state for a card answered at ``now``.

    ``now`` must be timezone-aware; the returned ``next_review_at`` is
    derived from it, keeping the calculation deterministic and testable.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if remembered:
        review_count = state.review_count + 1
        if review_count == 1:
            interval_days = FIRST_INTERVAL_DAYS
        elif review_count == 2:
            interval_days = SECOND_INTERVAL_DAYS
        else:
            interval_days = max(1, round(state.interval_days * state.ease_factor))
        ease_factor = state.ease_factor
    else:
        review_count = 0
        interval_days = FORGOT_INTERVAL_DAYS
        ease_factor = max(
            MIN_EASE_FACTOR, round(state.ease_factor * FORGOT_EASE_MULTIPLIER, 4)
        )
    return SrsUpdate(
        ease_factor=ease_factor,
        interval_days=interval_days,
        review_count=review_count,
        answered_at=now,
        next_review_at=now + timedelta(days=interval_days),
    )
