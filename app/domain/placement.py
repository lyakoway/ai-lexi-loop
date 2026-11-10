"""Placement test scoring: the rule that maps answers to a CEFR level.

The diagnostic asks N multiple-choice questions tagged with CEFR levels
(easy first, harder later). The recommendation climbs the level ladder:
starting at A1, it moves to the next level band while at least half of
that band's questions were answered correctly, and stops at the first
weak band. Deterministic and pure: fixed questions and answers always
produce the same level, which is what the confirmation step shows.
"""

from app.domain.profile import LEVEL_ORDER, Level

#: A level band is passed with at least this share of correct answers.
_PASS_RATIO = 0.5


def recommend_level(
    questions: list[tuple[str, bool]],
) -> str:
    """The recommended CEFR level from ``(question_level, is_correct)``.

    ``question_level`` is a CEFR key (``unknown`` entries are ignored in
    banding); the result is never ``unknown`` — a learner who fails
    everything is still placed at A1, the first learnable level.
    """
    bands: dict[str, list[bool]] = {}
    for level, answered in questions:
        if level == Level.UNKNOWN:
            continue
        bands.setdefault(level, []).append(answered)

    current = Level.A1.value
    ordered = [level for level in LEVEL_ORDER if level in bands]
    for band in ordered:
        correct = sum(bands[band])
        if correct / len(bands[band]) >= _PASS_RATIO:
            current = band
        else:
            break
    return current
