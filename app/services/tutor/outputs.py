"""Structured outputs of the AI tutor.

These Pydantic models define the contract between the LLM and the
application: a model answer is accepted only after validating against
them. ``extra="ignore"`` keeps parsing tolerant of harmless model
additions — correctness of the required fields is enforced, unknown
keys are simply dropped instead of triggering an expensive repair.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ExampleOut(BaseModel):
    """One natural example in the target language with its translation."""

    model_config = ConfigDict(extra="ignore")

    text: Annotated[BoundedText, StringConstraints(max_length=200)]
    translation: Annotated[BoundedText, StringConstraints(max_length=300)]


class DialogueLineOut(BaseModel):
    """One line of a mini-dialogue."""

    model_config = ConfigDict(extra="ignore")

    text: Annotated[BoundedText, StringConstraints(max_length=200)]
    translation: Annotated[BoundedText, StringConstraints(max_length=300)]


class MiniDialogueOut(BaseModel):
    """A short dialogue showing the expression in use; may be omitted."""

    model_config = ConfigDict(extra="ignore")

    lines: list[DialogueLineOut] = Field(min_length=2, max_length=6)


class ExplanationOut(BaseModel):
    """The tutor's explanation of one expression.

    IPA and phonetic hints are deliberately absent: they already exist
    on the dictionary card, and regenerating them would risk
    contradicting it.
    """

    model_config = ConfigDict(extra="ignore")

    meaning: Annotated[BoundedText, StringConstraints(max_length=300)]
    explanation: Annotated[BoundedText, StringConstraints(max_length=1500)]
    usage_notes: list[Annotated[BoundedText, StringConstraints(max_length=300)]] = (
        Field(default_factory=list, max_length=5)
    )
    common_mistakes: list[Annotated[BoundedText, StringConstraints(max_length=300)]] = (
        Field(default_factory=list, max_length=5)
    )
    examples: list[ExampleOut] = Field(min_length=1, max_length=5)
    mini_dialogue: MiniDialogueOut | None = None
