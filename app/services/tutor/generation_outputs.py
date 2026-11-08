"""Structured outputs of AI expression generation (Stage 6.3).

The model returns candidate cards; every field is bounded and every
candidate is re-validated deterministically before anything is stored.
``level`` uses the shared CEFR enum; ``topic`` the shared topic enum,
so an invalid category can never enter the catalogue.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.profile import Level, Topic

BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class GeneratedExpressionOut(BaseModel):
    """One candidate expression produced by the model."""

    model_config = ConfigDict(extra="ignore")

    text: Annotated[BoundedText, StringConstraints(max_length=128)]
    translation: Annotated[BoundedText, StringConstraints(max_length=256)]
    ipa: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] = ""
    phonetic_hint_ru: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=128)
    ] = ""
    example: Annotated[BoundedText, StringConstraints(max_length=1000)]
    example_translation: Annotated[BoundedText, StringConstraints(max_length=1000)]
    level: Level
    topic: Topic

    @field_validator("level")
    @classmethod
    def _level_must_not_be_unknown(cls, value: Level) -> Level:
        if value is Level.UNKNOWN:
            raise ValueError("generated level must be a concrete CEFR level")
        return value


class GeneratedSetOut(BaseModel):
    """The whole candidate set; duplicates inside are dropped downstream."""

    model_config = ConfigDict(extra="ignore")

    expressions: list[GeneratedExpressionOut] = Field(min_length=1, max_length=7)
