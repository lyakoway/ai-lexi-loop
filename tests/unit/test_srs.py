"""Deterministic tests for the spaced repetition algorithm."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.domain.srs import (
    FIRST_INTERVAL_DAYS,
    FORGOT_EASE_MULTIPLIER,
    INITIAL_EASE_FACTOR,
    MIN_EASE_FACTOR,
    SECOND_INTERVAL_DAYS,
    SrsState,
    apply_answer,
    utc_now,
)

NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)
EVEN_LATER = LATER + timedelta(days=6)


def test_initial_state_defaults() -> None:
    state = SrsState()
    assert state.ease_factor == INITIAL_EASE_FACTOR
    assert state.interval_days == FIRST_INTERVAL_DAYS
    assert state.review_count == 0


def test_first_remember_schedules_one_day() -> None:
    update = apply_answer(SrsState(), remembered=True, now=NOW)

    assert update.review_count == 1
    assert update.interval_days == 1
    assert update.ease_factor == INITIAL_EASE_FACTOR
    assert update.next_review_at == NOW + timedelta(days=1)
    assert update.answered_at == NOW


def test_second_remember_schedules_six_days() -> None:
    first = apply_answer(SrsState(), remembered=True, now=NOW)
    state = SrsState(
        ease_factor=first.ease_factor,
        interval_days=first.interval_days,
        review_count=first.review_count,
    )

    second = apply_answer(state, remembered=True, now=LATER)

    assert second.review_count == 2
    assert second.interval_days == SECOND_INTERVAL_DAYS
    assert second.next_review_at == LATER + timedelta(days=6)


def test_third_remember_multiplies_by_ease() -> None:
    state = SrsState(
        ease_factor=2.5, interval_days=SECOND_INTERVAL_DAYS, review_count=2
    )

    third = apply_answer(state, remembered=True, now=EVEN_LATER)

    assert third.review_count == 3
    assert third.interval_days == round(6 * 2.5)
    assert third.next_review_at == EVEN_LATER + timedelta(days=15)


def test_long_success_chain_grows_intervals() -> None:
    state = SrsState()
    moment = NOW
    intervals: list[int] = []
    for _ in range(5):
        update = apply_answer(state, remembered=True, now=moment)
        intervals.append(update.interval_days)
        state = SrsState(
            ease_factor=update.ease_factor,
            interval_days=update.interval_days,
            review_count=update.review_count,
        )
        moment = update.next_review_at
    assert intervals == [1, 6, 15, 38, 95]
    assert all(later > earlier for earlier, later in pairwise(intervals))


def test_forgot_resets_count_and_interval() -> None:
    state = SrsState(ease_factor=2.5, interval_days=15, review_count=3)

    update = apply_answer(state, remembered=False, now=NOW)

    assert update.review_count == 0
    assert update.interval_days == 1
    assert update.next_review_at == NOW + timedelta(days=1)
    assert update.ease_factor == pytest.approx(2.0)


def test_forgot_reduces_ease_with_floor() -> None:
    ease = INITIAL_EASE_FACTOR
    moment = NOW
    for expected in (2.0, 1.6, 1.3, 1.3):
        update = apply_answer(SrsState(ease_factor=ease), remembered=False, now=moment)
        assert update.ease_factor == pytest.approx(expected)
        ease = update.ease_factor
        moment = update.next_review_at
    assert ease == MIN_EASE_FACTOR


def test_remember_after_forgot_restarts_the_ladder() -> None:
    """A relearned card walks 1 → 6 days again, not its old interval."""
    forgotten = apply_answer(
        SrsState(ease_factor=2.5, interval_days=38, review_count=4),
        remembered=False,
        now=NOW,
    )
    state = SrsState(
        ease_factor=forgotten.ease_factor,
        interval_days=forgotten.interval_days,
        review_count=forgotten.review_count,
    )

    remembered_update = apply_answer(state, remembered=True, now=LATER)

    assert remembered_update.review_count == 1
    assert remembered_update.interval_days == 1
    assert remembered_update.next_review_at == LATER + timedelta(days=1)


def test_interval_never_drops_below_one_day() -> None:
    update = apply_answer(
        SrsState(ease_factor=MIN_EASE_FACTOR, interval_days=1, review_count=5),
        remembered=True,
        now=NOW,
    )
    assert update.interval_days >= 1


def test_naive_now_is_rejected() -> None:
    naive = datetime(2026, 11, 3, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_answer(SrsState(), remembered=True, now=naive)


def test_utc_now_returns_aware_datetime() -> None:
    moment = utc_now()
    assert moment.tzinfo is not None
    assert FORGOT_EASE_MULTIPLIER < 1  # sanity: forgetting gets harder cards
