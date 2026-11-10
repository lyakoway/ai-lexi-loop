"""Structured outputs of the level-placement diagnostic (Stage 6.4.7).

The model writes multiple-choice questions of ascending difficulty; every
question carries its CEFR level so the deterministic scoring rule
(:mod:`app.domain.placement`) can band the answers. ``correct_index`` is
never rendered to the user — the bot grades silently.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.profile import Level

BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_OPTIONS_MIN = 4
_OPTIONS_MAX = 4


class PlacementQuestionOut(BaseModel):
    """One diagnostic question with its options and level tag."""

    model_config = ConfigDict(extra="ignore")

    question: Annotated[BoundedText, StringConstraints(max_length=300)]
    options: list[Annotated[BoundedText, StringConstraints(max_length=120)]]
    correct_index: int = Field(ge=0, le=3)
    level: Level

    @field_validator("level")
    @classmethod
    def _level_must_be_concrete(cls, value: Level) -> Level:
        if value is Level.UNKNOWN:
            raise ValueError("diagnostic questions need a concrete CEFR level")
        return value


class PlacementSetOut(BaseModel):
    """The whole diagnostic; length comes from the language profile."""

    model_config = ConfigDict(extra="ignore")

    questions: list[PlacementQuestionOut] = Field(min_length=1, max_length=7)
