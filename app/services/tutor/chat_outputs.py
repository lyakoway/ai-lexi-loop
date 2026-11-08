"""Structured output of the AI tutor chat (Stage 6.4.2).

The model returns one JSON object per chat exchange. Only ``reply`` is
required; the correction/alternative fields are filled when there is
something to correct or improve — the Telegram/API renderers show
sections by presence. IPA/pronunciation fields are absent on purpose:
cards and the explain feature own that data.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChatReplyOut(BaseModel):
    """The tutor's structured answer to one chat message."""

    model_config = ConfigDict(extra="ignore")

    reply: Annotated[BoundedText, StringConstraints(max_length=1500)]
    correction: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=500)
    ] = ""
    correction_note: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=500)
    ] = ""
    better_alternative: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=500)
    ] = ""
    follow_up_question: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=300)
    ] = ""
