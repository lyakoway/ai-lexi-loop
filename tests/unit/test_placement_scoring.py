"""Unit tests for the placement scoring rule (Stage 6.4.7).

Pure and deterministic: fixed question levels + answers always map to
the same recommended CEFR level. The rule climbs the ladder while at
least half of a band's questions are correct and stops at the first
weak band; a learner who fails everything is still placed at A1.
"""

from app.domain.placement import recommend_level

A2 = "A2"
B1 = "B1"
B2 = "B2"
C1 = "C1"


def test_all_wrong_places_at_a1() -> None:
    verdicts = [(A2, False), (A2, False), (B1, False), (B2, False), (C1, False)]
    assert recommend_level(verdicts) == "A1"


def test_all_correct_reaches_highest_band() -> None:
    verdicts = [
        (A2, True),
        (A2, True),
        (B1, True),
        (B1, True),
        (B2, True),
        (B2, True),
        (C1, True),
    ]
    assert recommend_level(verdicts) == C1


def test_staircase_stops_at_first_weak_band() -> None:
    """Both A2 questions correct, B1 failed → A2 (B1+ never reached)."""
    verdicts = [(A2, True), (A2, True), (B1, False), (B1, False), (B2, True)]
    assert recommend_level(verdicts) == A2


def test_half_correct_band_is_passed() -> None:
    """1 of 2 correct in every band → the ladder climbs to the top."""
    verdicts = [
        (A2, True),
        (A2, False),
        (B1, True),
        (B1, False),
        (B2, True),
        (B2, False),
    ]
    assert recommend_level(verdicts) == B2


def test_failing_later_band_keeps_earlier_result() -> None:
    verdicts = [(A2, True), (B1, True), (B1, True), (B2, False), (C1, False)]
    assert recommend_level(verdicts) == B1


def test_unknown_level_questions_are_ignored() -> None:
    verdicts = [("unknown", False), (A2, True), (A2, True)]
    assert recommend_level(verdicts) == A2


def test_empty_input_places_at_a1() -> None:
    assert recommend_level([]) == "A1"


def test_result_is_deterministic() -> None:
    verdicts = [(A2, True), (B1, False), (B2, True), (C1, True)]
    assert recommend_level(verdicts) == recommend_level(list(reversed(verdicts)))
