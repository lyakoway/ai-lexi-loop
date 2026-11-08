"""AI tutor: orchestrated LLM scenarios (Stage 6+)."""

from app.services.tutor.chat import ChatService, ChatUnavailable
from app.services.tutor.chat_outputs import ChatReplyOut
from app.services.tutor.generation import (
    AcceptResult,
    GenerationDraftSet,
    GenerationService,
    GenerationUnavailable,
)
from app.services.tutor.generation_outputs import GeneratedSetOut
from app.services.tutor.outputs import ExplanationOut
from app.services.tutor.prompts import EXPLAIN_PROMPT_VERSION, GENERATE_PROMPT_VERSION
from app.services.tutor.service import (
    TutorExplanation,
    TutorService,
    TutorUnavailable,
)

__all__ = [
    "EXPLAIN_PROMPT_VERSION",
    "GENERATE_PROMPT_VERSION",
    "AcceptResult",
    "ChatReplyOut",
    "ChatService",
    "ChatUnavailable",
    "ExplanationOut",
    "GeneratedSetOut",
    "GenerationDraftSet",
    "GenerationService",
    "GenerationUnavailable",
    "TutorExplanation",
    "TutorService",
    "TutorUnavailable",
]
