"""Unit tests for the quiz grading output schema (Stage 6.4.6).

The schema enforces the cross-field contract: a correct answer uses
``error_type="none"`` and carries no correction; a wrong one names a
real error class and provides the corrected phrase. Violations fail
validation exactly like malformed JSON — the service then falls back to
the friendly "could not grade" path.
"""

import pytest
from pydantic import ValidationError

from app.services.quiz_outputs import QuizEvaluationOut


def test_correct_meaning_answer_is_valid() -> None:
    """Correct by meaning, different by form: error_type none, no fix."""
    verdict = QuizEvaluationOut(
        is_correct=True,
        error_type="none",
        explanation="Смысл верный, хотя формулировка отличается.",
        corrected="",
    )
    assert verdict.is_correct is True
    assert verdict.error_type == "none"


def test_correct_answer_with_error_type_is_invalid() -> None:
    with pytest.raises(ValidationError):
        QuizEvaluationOut(
            is_correct=True,
            error_type="grammar",
            explanation="всё верно",
            corrected="",
        )


@pytest.mark.parametrize("error_type", ["grammar", "vocabulary", "usage"])
def test_wrong_answer_with_real_error_type_is_valid(error_type: str) -> None:
    verdict = QuizEvaluationOut(
        is_correct=False,
        error_type=error_type,
        explanation="Краткое пояснение ошибки.",
        corrected="Break a leg",
    )
    assert verdict.is_correct is False
    assert verdict.error_type == error_type
    assert verdict.corrected == "Break a leg"


def test_wrong_answer_with_none_error_type_is_invalid() -> None:
    with pytest.raises(ValidationError):
        QuizEvaluationOut(
            is_correct=False,
            error_type="none",
            explanation="ошибка",
            corrected="Break a leg",
        )


def test_wrong_answer_without_correction_is_invalid() -> None:
    with pytest.raises(ValidationError):
        QuizEvaluationOut(
            is_correct=False,
            error_type="vocabulary",
            explanation="ошибка",
            corrected="",
        )


def test_unknown_error_type_is_invalid() -> None:
    with pytest.raises(ValidationError):
        QuizEvaluationOut(
            is_correct=True,
            error_type="spelling",
            explanation="верно",
            corrected="",
        )


def test_explanation_is_bounded() -> None:
    with pytest.raises(ValidationError):
        QuizEvaluationOut(
            is_correct=True,
            error_type="none",
            explanation="x" * 513,
            corrected="",
        )


def test_extra_fields_are_ignored() -> None:
    verdict = QuizEvaluationOut.model_validate(
        {
            "is_correct": True,
            "error_type": "none",
            "explanation": "верно",
            "corrected": "",
            "confidence": 0.99,
        }
    )
    assert verdict.is_correct is True
