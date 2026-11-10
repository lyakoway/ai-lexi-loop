"""Level-placement diagnostic service (Stage 6.4.7).

Generates one short multiple-choice test via the LLM when the learner
picks the "unknown level" option during onboarding. No database: the
questions live in the onboarding FSM draft, the deterministic scoring
rule is :func:`app.domain.placement.recommend_level`, and the learner
confirms (or rejects) the recommended level before anything is saved.

Unavailable LLM or invalid output degrades to a friendly message and
the manual level choice — onboarding never gets stuck.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings
from app.domain.languages import LanguageProfile, get_language_profile
from app.integrations.llm.client import StructuredLlmClient
from app.integrations.llm.exceptions import LlmError, LlmOutputFormatError
from app.integrations.llm.schemas import LlmMessage, LlmRequest
from app.services.placement_outputs import PlacementSetOut
from app.services.placement_prompts import (
    PLACEMENT_PROMPT_VERSION,
    build_placement_messages,
)

logger = logging.getLogger(__name__)

UnavailableReason = Literal["llm_disabled", "llm_error", "invalid_output"]


@dataclass(frozen=True)
class PlacementQuestion:
    """One question as shown to the learner (the key stays hidden)."""

    text: str
    options: tuple[str, ...]
    level: str
    correct_index: int


@dataclass(frozen=True)
class PlacementSet:
    """The generated diagnostic ready to run question by question."""

    questions: tuple[PlacementQuestion, ...]

    @property
    def size(self) -> int:
        return len(self.questions)


@dataclass(frozen=True)
class PlacementUnavailable:
    """Why no diagnostic could be generated (typed, no internals)."""

    reason: UnavailableReason


class PlacementService:
    """Builds placement tests; scoring lives in the domain layer."""

    def __init__(
        self,
        llm_client: StructuredLlmClient | None,
        settings: Settings,
    ) -> None:
        self._llm_client = llm_client
        self._settings = settings

    async def generate(
        self,
        *,
        target_language: str,
        native_language: str,
    ) -> PlacementSet | PlacementUnavailable:
        """Generate the diagnostic for the target language.

        The language profile drives the question count and the grammar
        briefing; unknown languages fall back to the generic profile.
        """
        if self._llm_client is None:
            return PlacementUnavailable(reason="llm_disabled")
        profile: LanguageProfile = get_language_profile(target_language)
        system, user = build_placement_messages(
            profile=profile, native_language=native_language
        )
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=system),
                LlmMessage(role="user", content=user),
            ],
            prompt_version=PLACEMENT_PROMPT_VERSION,
        )
        try:
            generated = await self._llm_client.complete_structured(
                request, PlacementSetOut
            )
        except LlmOutputFormatError:
            logger.warning("placement output invalid for %s", target_language)
            return PlacementUnavailable(reason="invalid_output")
        except LlmError as error:
            logger.warning(
                "placement llm error for %s error_type=%s",
                target_language,
                type(error).__name__,
            )
            return PlacementUnavailable(reason="llm_error")

        questions = tuple(
            PlacementQuestion(
                text=question.question,
                options=tuple(question.options),
                level=question.level.value,
                correct_index=question.correct_index,
            )
            for question in generated.questions
        )
        logger.info(
            "placement generated for %s: %s questions",
            target_language,
            len(questions),
        )
        return PlacementSet(questions=questions)
