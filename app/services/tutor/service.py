"""AI tutor service: the first scenario, Explain Expression.

The service is deliberately narrow — an orchestrated pipeline, not an
agent. It loads only the data needed for one explanation (the card and
the learner's languages/level), asks the LLM for a schema-validated
answer and never mutates learning state: explaining cannot touch SRS,
progress or the profile. Expected failures come back as
:class:`TutorUnavailable` reasons instead of exceptions, mirroring the
``LessonUnavailable`` convention; only storage errors propagate.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from app.db.models import Expression
from app.integrations.llm.client import StructuredLlmClient
from app.integrations.llm.exceptions import (
    LlmError,
    LlmOutputFormatError,
)
from app.integrations.llm.schemas import LlmMessage, LlmRequest
from app.repositories import (
    ExpressionProgressRepository,
    ExpressionRepository,
)
from app.services.analytics import (
    TUTOR_EXPLAINED,
    AnalyticsService,
    EventSource,
)
from app.services.profiles import ProfileService
from app.services.tutor.outputs import ExplanationOut
from app.services.tutor.prompts import (
    EXPLAIN_PROMPT_VERSION,
    build_explanation_messages,
)

logger = logging.getLogger(__name__)

UnavailableReason = Literal[
    "profile_not_found",
    "expression_not_found",
    "input_too_large",
    "llm_disabled",
    "llm_error",
    "invalid_output",
]

#: Defensive upper bounds; dictionary rows are already bounded by the
#: schema, this guards against future data sources.
_MAX_EXPRESSION_TEXT = 200
_MAX_EXPRESSION_EXAMPLE = 2000


@dataclass(frozen=True)
class TutorExplanation:
    """A successful explanation together with the card it explains."""

    explanation: ExplanationOut
    expression: Expression
    prompt_version: str


@dataclass(frozen=True)
class TutorUnavailable:
    """Why no explanation could be produced."""

    reason: UnavailableReason


class TutorService:
    """Produces AI explanations; read-only with respect to user data."""

    def __init__(
        self,
        profiles: ProfileService,
        expressions: ExpressionRepository,
        progress: ExpressionProgressRepository,
        llm_client: StructuredLlmClient | None,
        analytics: AnalyticsService,
    ) -> None:
        self._profiles = profiles
        self._expressions = expressions
        self._progress = progress
        self._llm_client = llm_client
        self._analytics = analytics

    async def explain_expression(
        self,
        user_id: int,
        expression_id: int,
        *,
        source: EventSource = "telegram",
    ) -> TutorExplanation | TutorUnavailable:
        """Explain one expression for the user, or say why it cannot.

        Ownership: only expressions already served to this user
        (a progress row exists) can be explained; anything else is
        reported as not found, without revealing whether it exists.
        """
        profile = await self._profiles.get_profile(user_id)
        if profile is None:
            return await self._unavailable(
                user_id, expression_id, "profile_not_found", source
            )
        expression = await self._expressions.get_by_id(expression_id)
        if expression is None:
            return await self._unavailable(
                user_id, expression_id, "expression_not_found", source
            )
        served = await self._progress.get_by_pair(user_id, expression_id)
        if served is None:
            return await self._unavailable(
                user_id, expression_id, "expression_not_found", source
            )
        if (
            len(expression.text) > _MAX_EXPRESSION_TEXT
            or len(expression.example) > _MAX_EXPRESSION_EXAMPLE
        ):
            return await self._unavailable(
                user_id, expression_id, "input_too_large", source
            )
        if self._llm_client is None:
            return await self._unavailable(
                user_id, expression_id, "llm_disabled", source
            )

        system, user = build_explanation_messages(
            expression_text=expression.text,
            known_translation=expression.translation,
            example=expression.example,
            example_translation=expression.example_translation,
            topic=expression.topic,
            level=profile.level,
            native_language=profile.native_language,
            target_language=profile.target_language,
        )
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=system),
                LlmMessage(role="user", content=user),
            ],
            prompt_version=EXPLAIN_PROMPT_VERSION,
        )
        try:
            explanation = await self._llm_client.complete_structured(
                request, ExplanationOut
            )
        except LlmOutputFormatError:
            logger.warning(
                "tutor explanation output invalid, user_id=%s expression_id=%s",
                user_id,
                expression_id,
            )
            return await self._unavailable(
                user_id, expression_id, "invalid_output", source
            )
        except LlmError as error:
            logger.warning(
                "tutor explanation llm error user_id=%s expression_id=%s "
                "error_type=%s",
                user_id,
                expression_id,
                type(error).__name__,
            )
            return await self._unavailable(user_id, expression_id, "llm_error", source)

        await self._analytics.track(
            user_id,
            TUTOR_EXPLAINED,
            source,
            {
                "expression_id": expression_id,
                "prompt_version": EXPLAIN_PROMPT_VERSION,
                "success": True,
            },
        )
        logger.info(
            "tutor explanation ok user_id=%s expression_id=%s", user_id, expression_id
        )
        return TutorExplanation(
            explanation=explanation,
            expression=expression,
            prompt_version=EXPLAIN_PROMPT_VERSION,
        )

    async def _unavailable(
        self,
        user_id: int,
        expression_id: int,
        reason: UnavailableReason,
        source: EventSource,
    ) -> TutorUnavailable:
        """Track the failed attempt and return the typed reason."""
        await self._analytics.track(
            user_id,
            TUTOR_EXPLAINED,
            source,
            {
                "expression_id": expression_id,
                "prompt_version": EXPLAIN_PROMPT_VERSION,
                "success": False,
                "error": reason,
            },
        )
        return TutorUnavailable(reason=reason)
