"""AI generation of personal expression drafts (Stage 6.3).

The service orchestrates: profile context → LLM (schema-validated) →
deterministic duplicate filtering → drafts stored for the requesting
user only. Generation never touches the shared catalogue, progress or
SRS; the acceptance flow (``accept_drafts``) is the only path that
creates catalogue cards, always owned by the requesting user, and the
only path that starts SRS scheduling — through the existing
``ExpressionProgressRepository`` so there is exactly one progress
mechanism.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from app.db.models import Expression, UserGeneratedExpression
from app.domain.srs import utc_now
from app.integrations.llm.client import StructuredLlmClient
from app.integrations.llm.exceptions import (
    LlmError,
    LlmOutputFormatError,
)
from app.integrations.llm.schemas import LlmMessage, LlmRequest
from app.repositories import (
    ExpressionRepository,
    UserGeneratedExpressionRepository,
)
from app.services.analytics import (
    TUTOR_ACCEPTED,
    TUTOR_GENERATED,
    AnalyticsService,
    EventSource,
)
from app.services.profiles import ProfileService
from app.services.tutor.generation_outputs import (
    GeneratedExpressionOut,
    GeneratedSetOut,
)
from app.services.tutor.prompts import (
    GENERATE_PROMPT_VERSION,
    MAX_GENERATED_EXPRESSIONS,
    build_generation_messages,
)

logger = logging.getLogger(__name__)

#: Hard input bound for the optional free-form situation text.
_MAX_SITUATION_LENGTH = 300

_WHITESPACE = re.compile(r"\s+")


def normalize_expression(text: str) -> str:
    """Case- and whitespace-insensitive key for duplicate checks.

    Deliberately not more aggressive: punctuation is part of real
    expressions ("Don't!"), and stripping it would merge distinct
    phrases.
    """
    return _WHITESPACE.sub(" ", text.strip().casefold())


UnavailableReason = Literal[
    "profile_not_found",
    "input_too_large",
    "llm_disabled",
    "llm_error",
    "invalid_output",
]


@dataclass(frozen=True)
class GenerationDraftSet:
    """A persisted set of drafts (what the user will review)."""

    drafts: list[UserGeneratedExpression]
    duplicates_skipped: int


@dataclass(frozen=True)
class GenerationUnavailable:
    """Why no generation could be produced."""

    reason: UnavailableReason


@dataclass(frozen=True)
class AcceptResult:
    """Outcome of accepting drafts."""

    accepted: list[Expression] = field(default_factory=list)
    rejected_ids: list[int] = field(default_factory=list)
    skipped_ids: list[int] = field(default_factory=list)


class GenerationService:
    """Generates personal drafts and accepts them into the catalogue."""

    def __init__(
        self,
        profiles: ProfileService,
        expressions: ExpressionRepository,
        drafts: UserGeneratedExpressionRepository,
        llm_client: StructuredLlmClient | None,
        analytics: AnalyticsService,
    ) -> None:
        self._profiles = profiles
        self._expressions = expressions
        self._drafts = drafts
        self._llm_client = llm_client
        self._analytics = analytics

    async def generate_drafts(
        self,
        user_id: int,
        *,
        topic: str,
        count: int,
        situation: str = "",
        source: EventSource = "telegram",
    ) -> GenerationDraftSet | GenerationUnavailable:
        """Generate up to ``count`` drafts for the user.

        Deterministic duplicate filtering drops candidates that already
        exist in the shared catalogue or among the user's drafts (case-
        and whitespace-insensitive); duplicates inside one LLM answer
        are dropped as well.
        """
        if len(situation) > _MAX_SITUATION_LENGTH:
            return await self._unavailable(
                user_id, "input_too_large", source, topic, count
            )
        profile = await self._profiles.get_profile(user_id)
        if profile is None:
            return await self._unavailable(
                user_id, "profile_not_found", source, topic, count
            )
        if self._llm_client is None:
            return await self._unavailable(
                user_id, "llm_disabled", source, topic, count
            )
        bounded_count = max(1, min(count, MAX_GENERATED_EXPRESSIONS))

        system, user = build_generation_messages(
            count=bounded_count,
            topic=topic,
            level=profile.level,
            native_language=profile.native_language,
            target_language=profile.target_language,
            situation=situation,
        )
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=system),
                LlmMessage(role="user", content=user),
            ],
            prompt_version=GENERATE_PROMPT_VERSION,
        )
        try:
            generated = await self._llm_client.complete_structured(
                request, GeneratedSetOut
            )
        except LlmOutputFormatError:
            logger.warning("generation output invalid, user_id=%s", user_id)
            return await self._unavailable(
                user_id, "invalid_output", source, topic, bounded_count
            )
        except LlmError as error:
            logger.warning(
                "generation llm error user_id=%s error_type=%s",
                user_id,
                type(error).__name__,
            )
            return await self._unavailable(
                user_id, "llm_error", source, topic, bounded_count
            )

        candidates = generated.expressions
        # In-set duplicates (normalize + first wins), then duplicates
        # against the catalogue and the user's earlier drafts.
        seen: set[str] = set()
        unique_candidates: list[GeneratedExpressionOut] = []
        for candidate in candidates:
            key = normalize_expression(candidate.text)
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(candidate)

        catalogue_hits = await self._expressions.find_existing_texts(
            profile.target_language, [c.text for c in unique_candidates]
        )
        user_draft_hits = await self._drafts.count_user_draft_texts(
            user_id, [normalize_expression(c.text) for c in unique_candidates]
        )
        duplicates_skipped = 0
        rows: list[UserGeneratedExpression] = []
        for candidate in unique_candidates:
            key = normalize_expression(candidate.text)
            if candidate.text.lower() in catalogue_hits or key in user_draft_hits:
                duplicates_skipped += 1
                continue
            rows.append(
                UserGeneratedExpression(
                    user_id=user_id,
                    language=profile.target_language,
                    native_language=profile.native_language,
                    text=candidate.text,
                    translation=candidate.translation,
                    ipa=candidate.ipa,
                    phonetic_hint_ru=candidate.phonetic_hint_ru,
                    example=candidate.example,
                    example_translation=candidate.example_translation,
                    level=candidate.level.value,
                    topic=candidate.topic.value,
                    source="ai_generated",
                    prompt_version=GENERATE_PROMPT_VERSION,
                    status="draft",
                )
            )

        # Nothing left after deduplication: skip the pointless write.
        saved = await self._drafts.save_drafts(rows) if rows else []
        await self._analytics.track(
            user_id,
            TUTOR_GENERATED,
            source,
            {
                "prompt_version": GENERATE_PROMPT_VERSION,
                "topic": topic,
                "requested_count": bounded_count,
                "saved_count": len(saved),
                "duplicates_skipped": duplicates_skipped,
                "success": True,
            },
        )
        logger.info(
            "generation saved user_id=%s saved=%s duplicates=%s",
            user_id,
            len(saved),
            duplicates_skipped,
        )
        return GenerationDraftSet(drafts=saved, duplicates_skipped=duplicates_skipped)

    async def accept_drafts(
        self,
        user_id: int,
        draft_ids: list[int],
        *,
        source: EventSource = "telegram",
    ) -> AcceptResult:
        """Accept the user's drafts into the catalogue, atomically.

        Each accepted draft becomes a catalogue card owned by the user
        (or reuses an identical existing card); the card then enters the
        normal lesson flow — the next ``/lesson`` serves it as a new
        expression and the standard SRS scheduling starts at that point,
        exactly like for shared catalogue cards. No progress rows are
        created here: there is exactly one progress mechanism.

        Only the owner's own drafts in ``draft`` status are processed;
        everything else is skipped without error. Runs in the caller's
        transaction — a database failure leaves nothing half-saved.
        """
        now = utc_now()
        result = AcceptResult()
        drafts = await self._drafts.get_by_ids(user_id, draft_ids)
        drafts_by_id = {draft.id: draft for draft in drafts}

        for draft_id in draft_ids:
            draft = drafts_by_id.get(draft_id)
            if draft is None or draft.status != "draft":
                result.skipped_ids.append(draft_id)
                continue

            existing = await self._expressions.get_by_language_and_text(
                draft.language, draft.text
            )
            if existing is not None:
                # Same expression already exists (shared catalogue or
                # the user's own earlier card): learn that card instead
                # of duplicating it.
                card = existing
            else:
                card = await self._expressions.add(
                    Expression(
                        owner_user_id=user_id,
                        language=draft.language,
                        text=draft.text,
                        translation=draft.translation,
                        ipa=draft.ipa,
                        phonetic_hint_ru=draft.phonetic_hint_ru,
                        example=draft.example,
                        example_translation=draft.example_translation,
                        level=draft.level,
                        topic=draft.topic,
                    )
                )

            await self._drafts.mark_accepted(
                draft.id, accepted_expression_id=card.id, now=now
            )
            result.accepted.append(card)
            logger.info(
                "draft accepted user_id=%s draft=%s expression=%s",
                user_id,
                draft.id,
                card.id,
            )

        await self._analytics.track(
            user_id,
            TUTOR_ACCEPTED,
            source,
            {
                "accepted_count": len(result.accepted),
                "skipped_count": len(result.skipped_ids),
                "prompt_version": GENERATE_PROMPT_VERSION,
            },
        )
        return result

    async def reject_drafts(
        self,
        user_id: int,
        draft_ids: list[int],
        *,
        source: EventSource = "telegram",
    ) -> int:
        """Reject the user's drafts; rejected cards never enter lessons."""
        now = utc_now()
        drafts = await self._drafts.get_by_ids(user_id, draft_ids)
        rejected = 0
        for draft in drafts:
            if draft.status != "draft":
                continue
            await self._drafts.mark_rejected(draft.id, now=now)
            rejected += 1
        logger.info("drafts rejected user_id=%s count=%s", user_id, rejected)
        del source  # no separate event: generation/accept cover analytics
        return rejected

    async def _unavailable(
        self,
        user_id: int,
        reason: UnavailableReason,
        source: EventSource,
        topic: str,
        count: int,
    ) -> GenerationUnavailable:
        await self._analytics.track(
            user_id,
            TUTOR_GENERATED,
            source,
            {
                "prompt_version": GENERATE_PROMPT_VERSION,
                "topic": topic,
                "requested_count": count,
                "saved_count": 0,
                "success": False,
                "error": reason,
            },
        )
        return GenerationUnavailable(reason=reason)
