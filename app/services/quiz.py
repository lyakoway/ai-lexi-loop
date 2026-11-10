"""Active recall quiz (Stage 6.4.6).

One question per ``/quiz``: the bot shows the Russian translation, the
learner writes a free-form answer in the target language, the LLM grades
the MEANING (not the exact form). The verdict feeds the existing SM-2
scheduler (the same ``apply_answer``/``update_srs`` path reviews use),
the consecutive-success counter on the profile and — only after N
correct answers in a row plus the user's explicit confirmation — a CEFR
promotion. One good answer never changes the level.

A failed evaluation (invalid model output, LLM error) never breaks the
quiz: the learner gets a friendly retry message, the schedule and the
streak stay untouched. Telemetry (``quiz_evaluations``) is best-effort
in its own session and stores metrics only — never the answer text.

The "top forgotten" report (most lapses, then most overdue) is throttled
to once per ``forgotten_report_interval_days`` via
``profiles.last_forgotten_report_at``.

Transaction boundaries are short: the grading LLM call runs outside any
transaction, the schedule/streak write and the telemetry write each use
their own session (mirroring :mod:`app.services.tts`).
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.core.config import Settings
from app.db.session import SessionFactory, session_scope
from app.domain.profile import next_level
from app.domain.srs import SrsState, SrsUpdate, apply_answer, utc_now
from app.integrations.llm.client import StructuredLlmClient
from app.integrations.llm.exceptions import LlmError, LlmOutputFormatError
from app.integrations.llm.schemas import LlmMessage, LlmRequest
from app.repositories import (
    ExpressionProgressRepository,
    ExpressionRepository,
    ProfileRepository,
    QuizEvaluationRepository,
)
from app.services.quiz_outputs import QuizEvaluationOut
from app.services.quiz_prompts import QUIZ_PROMPT_VERSION, build_quiz_grading_messages

logger = logging.getLogger(__name__)

#: Hard input bound for the free-form answer (LLM cost + prompt safety).
_MAX_ANSWER_LENGTH = 500

#: Size of the weekly "top forgotten" report.
FORGOTTEN_REPORT_SIZE = 10

StartUnavailableReason = Literal["no_words", "profile_not_found"]

GradeUnavailableReason = Literal[
    "expression_not_found",
    "answer_too_long",
    "llm_disabled",
    "llm_error",
    "invalid_output",
]


@dataclass(frozen=True)
class QuizQuestion:
    """The question served to the learner (never the answer itself)."""

    expression_id: int
    question_translation: str
    level: str


@dataclass(frozen=True)
class QuizGradeResult:
    """The grading verdict plus everything the flow needs to render."""

    is_correct: bool
    error_type: str
    explanation: str
    corrected: str
    level_offer: bool
    streak: int
    srs: SrsUpdate | None


@dataclass(frozen=True)
class QuizUnavailable:
    """Why the quiz could not proceed (typed, no internals)."""

    reason: StartUnavailableReason | GradeUnavailableReason


@dataclass(frozen=True)
class ForgottenItem:
    """One card of the "top forgotten" report."""

    text: str
    translation: str
    lapse_count: int
    interval_days: int


@dataclass(frozen=True)
class ForgottenReport:
    """The throttled weekly report; empty means nothing was forgotten."""

    items: list[ForgottenItem]


@dataclass(frozen=True)
class ForgottenThrottled:
    """The report was shown recently; the next allowed moment."""

    next_allowed_at: datetime


class QuizService:
    """Grades free-form answers, schedules reviews and guards levels."""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm_client: StructuredLlmClient | None,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._llm_client = llm_client
        self._settings = settings

    async def start_quiz(
        self, user_id: int, *, now: datetime | None = None
    ) -> QuizQuestion | QuizUnavailable:
        """Pick the question: the oldest due card, else the least
        recently shown served card. Without any served words there is
        nothing to quiz."""
        moment = now or utc_now()
        async with session_scope(self._session_factory) as session:
            progress_repo = ExpressionProgressRepository(session)
            expression_repo = ExpressionRepository(session)
            due = await progress_repo.get_due(user_id, now=moment, limit=1)
            progress = due[0] if due else None
            if progress is None:
                progress = await progress_repo.get_least_recently_shown(user_id)
            if progress is None:
                return QuizUnavailable(reason="no_words")
            expression = await expression_repo.get_by_id(progress.expression_id)
            if expression is None:
                logger.warning(
                    "quiz for user_id=%s: served expression %s disappeared",
                    user_id,
                    progress.expression_id,
                )
                return QuizUnavailable(reason="no_words")
            return QuizQuestion(
                expression_id=expression.id,
                question_translation=expression.translation,
                level=expression.level,
            )

    async def grade_answer(
        self,
        user_id: int,
        expression_id: int,
        answer: str,
        *,
        now: datetime | None = None,
    ) -> QuizGradeResult | QuizUnavailable:
        """Grade one free-form answer and apply the scheduling result.

        The schedule and the streak change only on a produced verdict;
        an unavailable grading leaves both untouched.
        """
        moment = now or utc_now()
        if len(answer) > _MAX_ANSWER_LENGTH:
            return QuizUnavailable(reason="answer_too_long")

        async with session_scope(self._session_factory) as session:
            progress = await ExpressionProgressRepository(session).get_by_pair(
                user_id, expression_id
            )
            if progress is None:
                return QuizUnavailable(reason="expression_not_found")
            expression = await ExpressionRepository(session).get_by_id(expression_id)
            if expression is None:
                return QuizUnavailable(reason="expression_not_found")
            profile = await ProfileRepository(session).get_by_user_id(user_id)
            if profile is None:
                return QuizUnavailable(reason="expression_not_found")
            context = _GradingContext(
                native_language=profile.native_language,
                target_language=profile.target_language,
                level=profile.level,
                expected=expression.text,
                question=expression.translation,
            )

        if self._llm_client is None:
            return QuizUnavailable(reason="llm_disabled")

        system, user = build_quiz_grading_messages(
            expression_text=context.expected,
            question_translation=context.question,
            learner_answer=answer,
            native_language=context.native_language,
            target_language=context.target_language,
            level=context.level,
        )
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=system),
                LlmMessage(role="user", content=user),
            ],
            prompt_version=QUIZ_PROMPT_VERSION,
        )
        started = time.monotonic()
        try:
            verdict = await self._llm_client.complete_structured(
                request, QuizEvaluationOut
            )
        except LlmOutputFormatError:
            await self._record(
                user_id=user_id,
                expression_id=expression_id,
                is_correct=None,
                error_type=None,
                started=started,
                error_kind="invalid_output",
            )
            logger.warning("quiz grading output invalid user_id=%s", user_id)
            return QuizUnavailable(reason="invalid_output")
        except LlmError as error:
            await self._record(
                user_id=user_id,
                expression_id=expression_id,
                is_correct=None,
                error_type=None,
                started=started,
                error_kind=type(error).__name__,
            )
            logger.warning(
                "quiz grading llm error user_id=%s error_type=%s",
                user_id,
                type(error).__name__,
            )
            return QuizUnavailable(reason="llm_error")
        latency_ms = int((time.monotonic() - started) * 1000)

        level_offer = False
        streak = 0
        async with session_scope(self._session_factory) as session:
            progress_repo = ExpressionProgressRepository(session)
            profile_repo = ProfileRepository(session)
            progress = await progress_repo.get_by_pair(user_id, expression_id)
            srs: SrsUpdate | None = None
            if progress is not None:
                srs = apply_answer(
                    SrsState(
                        ease_factor=progress.ease_factor,
                        interval_days=progress.interval_days,
                        review_count=progress.review_count,
                    ),
                    remembered=verdict.is_correct,
                    now=moment,
                )
                await progress_repo.update_srs(progress, srs)
            profile = await profile_repo.get_by_user_id(user_id)
            if profile is not None:
                streak = profile.quiz_success_streak + 1 if verdict.is_correct else 0
                await profile_repo.set_quiz_streak(profile, streak)
                level_offer = (
                    verdict.is_correct
                    and streak >= self._settings.quiz_level_streak_needed
                    and next_level(profile.level) is not None
                )

        await self._record(
            user_id=user_id,
            expression_id=expression_id,
            is_correct=verdict.is_correct,
            error_type=verdict.error_type,
            started=started,
            latency_ms=latency_ms,
        )
        logger.info(
            "quiz graded user_id=%s expression_id=%s correct=%s error=%s",
            user_id,
            expression_id,
            verdict.is_correct,
            verdict.error_type,
        )
        return QuizGradeResult(
            is_correct=verdict.is_correct,
            error_type=verdict.error_type,
            explanation=verdict.explanation,
            corrected=verdict.corrected,
            level_offer=level_offer,
            streak=streak,
            srs=srs,
        )

    async def confirm_level_up(self, user_id: int) -> str | None:
        """Apply the confirmed CEFR promotion.

        Returns the new level, or ``None`` when the promotion is not
        deserved anymore (the streak was reset by a wrong answer) or the
        user is already at C2. The LLM never raises the level on its own.
        """
        async with session_scope(self._session_factory) as session:
            profile_repo = ProfileRepository(session)
            profile = await profile_repo.get_by_user_id(user_id)
            if profile is None:
                return None
            new_level = next_level(profile.level)
            if (
                profile.quiz_success_streak < self._settings.quiz_level_streak_needed
                or new_level is None
            ):
                return None
            await profile_repo.promote_level(profile, new_level)
            logger.info(
                "quiz level promoted user_id=%s new_level=%s", user_id, new_level
            )
            return new_level

    async def forgotten_report(
        self, user_id: int, *, now: datetime | None = None
    ) -> ForgottenReport | ForgottenThrottled:
        """The "top forgotten" report, shown at most once per interval.

        The throttle is stamped only when a non-empty report is actually
        shown: an empty result keeps the next request free.
        """
        moment = now or utc_now()
        interval = timedelta(days=self._settings.forgotten_report_interval_days)
        async with session_scope(self._session_factory) as session:
            profile_repo = ProfileRepository(session)
            profile = await profile_repo.get_by_user_id(user_id)
            if profile is None:
                return ForgottenReport(items=[])
            last = profile.last_forgotten_report_at
            if last is not None and moment < last + interval:
                return ForgottenThrottled(next_allowed_at=last + interval)
            pairs = await ExpressionProgressRepository(session).get_forgotten_top(
                user_id, limit=FORGOTTEN_REPORT_SIZE
            )
            if not pairs:
                return ForgottenReport(items=[])
            items = [
                ForgottenItem(
                    text=expression.text,
                    translation=expression.translation,
                    lapse_count=progress.lapse_count,
                    interval_days=progress.interval_days,
                )
                for progress, expression in pairs
            ]
            await profile_repo.set_last_forgotten_report_at(profile, moment)
            logger.info("forgotten report user_id=%s items=%s", user_id, len(items))
            return ForgottenReport(items=items)

    async def _record(
        self,
        *,
        user_id: int,
        expression_id: int,
        is_correct: bool | None,
        error_type: str | None,
        started: float,
        latency_ms: int | None = None,
        error_kind: str | None = None,
    ) -> None:
        """Best-effort ``quiz_evaluations`` row in its own short session.

        A database failure here is logged and never changes the result
        shown to the learner. Metrics only: the answer text is not
        stored anywhere in this contour.
        """
        measured = (
            latency_ms
            if latency_ms is not None
            else int((time.monotonic() - started) * 1000)
        )
        try:
            async with session_scope(self._session_factory) as session:
                await QuizEvaluationRepository(session).record(
                    user_id=user_id,
                    expression_id=expression_id,
                    is_correct=is_correct,
                    error_type=error_type,
                    latency_ms=measured,
                    error_kind=error_kind,
                )
        except Exception:
            logger.warning("quiz telemetry write failed (ignored)")


@dataclass(frozen=True)
class _GradingContext:
    """Fields the grading prompt needs, captured in one short session."""

    native_language: str
    target_language: str
    level: str
    expected: str
    question: str
