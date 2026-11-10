"""Structured output of quiz answer grading (Stage 6.4.6).

The model judges the MEANING of the learner's free-form answer against
the expected expression, not the exact form: a paraphrase or synonym
that preserves the meaning counts as correct. All fields are bounded;
the cross-field rules (a correct answer has ``error_type="none"``, a
wrong one carries a real error class and a non-empty correction) are
part of the schema, so a sloppy model answer fails validation and goes
through the standard one-shot repair, then the ``invalid_output``
fallback.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

QuizErrorType = Literal["grammar", "vocabulary", "usage", "none"]


class QuizEvaluationOut(BaseModel):
    """The grading verdict for one free-form quiz answer."""

    model_config = ConfigDict(extra="ignore")

    is_correct: bool
    error_type: QuizErrorType
    explanation: Annotated[BoundedText, StringConstraints(max_length=512)]
    corrected: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=256)
    ] = ""

    @model_validator(mode="after")
    def _verdict_consistency(self) -> "QuizEvaluationOut":
        if self.is_correct:
            if self.error_type != "none":
                raise ValueError('a correct answer must use error_type="none"')
        else:
            if self.error_type == "none":
                raise ValueError("a wrong answer must name a real error_type")
            if not self.corrected:
                raise ValueError("a wrong answer must provide the corrected phrase")
        return self
