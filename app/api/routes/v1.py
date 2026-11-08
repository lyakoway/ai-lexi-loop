"""Public HTTP API v1 for non-Telegram clients.

Every endpoint requires a personal bearer token and operates only on
the authenticated user's data; ownership is enforced inside services by
user_id-scoped queries. Business logic is shared with Telegram handlers
one-to-one — no duplication.
"""

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    ChatDep,
    CurrentUserId,
    GenerationDep,
    IdempotencyKeyDep,
    LessonDep,
    ProfileDep,
    ReviewDep,
    StatsDep,
    TutorDep,
)
from app.db.models import Expression, Profile, TutorConversation, TutorMessage
from app.schemas.api import (
    AcceptIn,
    AcceptOut,
    ChatConversationOut,
    ChatHistoryOut,
    ChatMessageIn,
    ChatMessageOut,
    ChatSendOut,
    ChatStartIn,
    DueReviewsOut,
    GeneratedDraftOut,
    GeneratedOut,
    GenerateIn,
    LessonCardOut,
    LessonOut,
    ProfileOut,
    ReviewAnswerIn,
    ReviewAnswerOut,
    StatsOverviewOut,
    StatsProgressOut,
)
from app.schemas.profile import ProfileUpdate
from app.services import ProfileNotConfiguredError
from app.services.lessons import LessonPlan
from app.services.reviews import ReviewUnavailable
from app.services.tutor import (
    ChatUnavailable,
    GenerationUnavailable,
    TutorExplanation,
)
from app.services.tutor.outputs import ExplanationOut

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


def _card_out(expression: Expression) -> LessonCardOut:
    return LessonCardOut(
        expression_id=expression.id,
        text=expression.text,
        translation=expression.translation,
        ipa=expression.ipa,
        phonetic_hint_ru=expression.phonetic_hint_ru,
        example=expression.example,
        example_translation=expression.example_translation,
        level=expression.level,
        topic=expression.topic,
    )


async def _require_profile(user_id: int, profiles: ProfileDep) -> Profile:
    profile = await profiles.get_profile(user_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    return profile


@router.get("/profile", response_model=ProfileOut)
async def get_profile(user_id: CurrentUserId, profiles: ProfileDep) -> Profile:
    """Return the user's profile."""
    return await _require_profile(user_id, profiles)


@router.patch("/profile", response_model=ProfileOut)
async def patch_profile(
    user_id: CurrentUserId,
    profiles: ProfileDep,
    update: ProfileUpdate,
) -> Profile:
    """Change individual profile settings."""
    await _require_profile(user_id, profiles)
    try:
        return await profiles.update_profile(user_id, update, source="web")
    except ProfileNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        ) from None


@router.post("/lessons", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def create_lesson(
    user_id: CurrentUserId, profiles: ProfileDep, lessons: LessonDep
) -> LessonOut:
    """Build a new lesson from the user's profile.

    Returns cards, or the same shape with ``unavailable_reason`` set when
    nothing new matches the profile (topics, level or exhausted
    dictionary).
    """
    profile = await _require_profile(user_id, profiles)
    plan = await lessons.build_lesson(user_id, profile, source="web")
    if isinstance(plan, LessonPlan):
        return LessonOut(
            lesson_ref=plan.lesson_ref,
            used_default_level=plan.used_default_level,
            expressions=[_card_out(expression) for expression in plan.expressions],
        )
    return LessonOut(unavailable_reason=plan.reason)


@router.get("/reviews/due", response_model=DueReviewsOut)
async def due_reviews(user_id: CurrentUserId, reviews: ReviewDep) -> DueReviewsOut:
    """Cards whose review is due, oldest first (bounded by the batch size)."""
    plan = await reviews.build_review(user_id, source="web")
    if isinstance(plan, ReviewUnavailable):
        return DueReviewsOut(cards=[])
    return DueReviewsOut(
        cards=[_card_out(expression) for expression in plan.expressions]
    )


@router.post("/reviews/{expression_id}/answer", response_model=ReviewAnswerOut)
async def answer_review(
    user_id: CurrentUserId,
    expression_id: int,
    answer: ReviewAnswerIn,
    reviews: ReviewDep,
) -> ReviewAnswerOut:
    """Apply one spaced-repetition answer.

    Each call is one answer: clients send it exactly once per card, the
    same way Telegram answers once per button press.
    """
    update = await reviews.apply_answer(
        user_id, expression_id, remembered=answer.remembered, source="web"
    )
    if update is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Expression is not in your progress",
        )
    return ReviewAnswerOut(
        expression_id=expression_id,
        review_count=update.review_count,
        interval_days=update.interval_days,
        ease_factor=update.ease_factor,
        next_review_at=update.next_review_at,
    )


@router.get("/stats/overview", response_model=StatsOverviewOut)
async def stats_overview(user_id: CurrentUserId, stats: StatsDep) -> StatsOverviewOut:
    """Headline learning statistics of the authenticated user."""
    overview = await stats.get_overview(user_id)
    return StatsOverviewOut(
        expressions_shown=overview.expressions_shown,
        expressions_completed=overview.expressions_completed,
        lessons_completed=overview.lessons_completed,
        reviews_total=overview.reviews_total,
        remember_ratio=overview.remember_ratio,
        due_count=overview.due_count,
        streak_days=overview.streak_days,
    )


@router.get("/stats/progress", response_model=StatsProgressOut)
async def stats_progress(user_id: CurrentUserId, stats: StatsDep) -> StatsProgressOut:
    """Daily activity for the last 30 days."""
    progress = await stats.get_progress(user_id)
    return StatsProgressOut(
        days=[
            {
                "day": day.day,
                "expressions_shown": day.expressions_shown,
                "reviews": day.reviews,
            }
            for day in progress
        ]
    )


@router.post(
    "/expressions/{expression_id}/explain",
    response_model=ExplanationOut,
)
async def explain_expression(
    user_id: CurrentUserId,
    expression_id: int,
    tutor: TutorDep,
) -> ExplanationOut | JSONResponse:
    """AI explanation of one served expression, tuned to the profile.

    Error paths return a JSONResponse directly so the tracked
    tutor_explained event survives the request transaction.
    """
    result = await tutor.explain_expression(user_id, expression_id, source="web")
    if isinstance(result, TutorExplanation):
        return result.explanation
    # Error paths return responses instead of raising: the tracked
    # tutor_explained event shares the request transaction and would be
    # rolled back by a raised HTTPException.
    if result.reason in ("expression_not_found", "profile_not_found"):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Expression not found"},
        )
    # llm_disabled / llm_error / invalid_output / input_too_large
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "AI features are unavailable right now"},
    )


@router.post("/tutor/expressions/generate", response_model=GeneratedOut)
async def generate_expressions(
    user_id: CurrentUserId,
    request: GenerateIn,
    generation: GenerationDep,
) -> GeneratedOut | JSONResponse:
    """Generate personal expression drafts for the user.

    Drafts are private until accepted; identical candidates (in the
    catalogue or among the user's drafts) are filtered out
    deterministically.
    """
    result = await generation.generate_drafts(
        user_id,
        topic=request.topic,
        count=request.count,
        situation=request.situation,
        source="web",
    )
    if isinstance(result, GenerationUnavailable):
        if result.reason in ("profile_not_found",):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Profile not found"},
            )
        # input_too_large / llm_disabled / llm_error / invalid_output
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "AI features are unavailable right now",
                "reason": result.reason,
            },
        )
    return GeneratedOut(
        drafts=[
            GeneratedDraftOut(
                draft_id=draft.id,
                text=draft.text,
                translation=draft.translation,
                ipa=draft.ipa,
                phonetic_hint_ru=draft.phonetic_hint_ru,
                example=draft.example,
                example_translation=draft.example_translation,
                level=draft.level,
                topic=draft.topic,
            )
            for draft in result.drafts
        ],
        duplicates_skipped=result.duplicates_skipped,
    )


@router.post("/tutor/expressions/accept", response_model=AcceptOut)
async def accept_expressions(
    user_id: CurrentUserId,
    request: AcceptIn,
    generation: GenerationDep,
) -> AcceptOut:
    """Accept the user's drafts: catalogue cards + SRS scheduling.

    Only the caller's own drafts in ``draft`` status are processed;
    unknown or already-processed ids are reported in ``skipped``.
    """
    result = await generation.accept_drafts(user_id, request.draft_ids, source="web")
    return AcceptOut(
        accepted_expression_ids=[card.id for card in result.accepted],
        skipped_draft_ids=result.skipped_ids,
    )


# ---------------------------------------------------------------------------
# AI Tutor Chat (Stage 6.4.3)
# ---------------------------------------------------------------------------


def _chat_conversation_out(conversation: TutorConversation) -> ChatConversationOut:
    return ChatConversationOut(
        id=conversation.id,
        language=conversation.language,
        native_language=conversation.native_language,
        level=conversation.level,
        topic=conversation.topic,
        situation=conversation.situation,
        status=conversation.status,
        message_count=conversation.message_count,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        closed_at=conversation.closed_at,
    )


def _chat_message_out(message: TutorMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        text=message.text,
        client_request_id=message.client_request_id,
        in_reply_to_message_id=message.in_reply_to_message_id,
        created_at=message.created_at,
    )


def _chat_error(reason: str) -> JSONResponse:
    """Map a ChatService reason to an HTTP response (typed, no internals)."""
    status_code = _CHAT_REASON_STATUS.get(reason, 503)
    return JSONResponse(
        status_code=status_code,
        content={"detail": reason},
    )


_CHAT_REASON_STATUS: dict[str, int] = {
    "profile_not_found": 404,
    "conversation_not_found": 404,
    "conversation_closed": 409,
    "busy": 409,
    "rate_limited": 429,
    "claim_lost": 409,
    "message_too_long": 422,
    "idempotency_conflict": 409,
    "idempotency_pending": 409,
    "llm_disabled": 503,
    "llm_error": 503,
    "invalid_output": 503,
}


@router.post("/tutor/chat", response_model=ChatConversationOut)
async def start_tutor_chat(
    user_id: CurrentUserId,
    chats: ChatDep,
    start: ChatStartIn | None = None,
) -> ChatConversationOut | JSONResponse:
    """Create or resume the caller's active tutor chat dialog.

    Languages and CEFR level come from the authenticated user's profile.
    Never creates a second active dialog.
    """
    payload = start or ChatStartIn()
    conversation = await chats.start_chat(
        user_id,
        topic=payload.topic,
        situation=payload.situation,
        source="web",
    )
    if isinstance(conversation, ChatUnavailable):
        # profile_not_found
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Profile not found"},
        )
    # Create-or-get semantics: 200 for both a new and a resumed dialog.
    return _chat_conversation_out(conversation)


@router.get("/tutor/chat", response_model=list[ChatConversationOut])
async def list_tutor_chats(
    user_id: CurrentUserId, chats: ChatDep
) -> list[ChatConversationOut]:
    """All tutor chat dialogs of the caller, newest first."""
    conversations = await chats.list_chats(user_id)
    return [_chat_conversation_out(conversation) for conversation in conversations]


@router.get("/tutor/chat/active", response_model=ChatConversationOut)
async def get_active_tutor_chat(
    user_id: CurrentUserId, chats: ChatDep
) -> ChatConversationOut:
    """The caller's active tutor chat dialog, or 404."""
    conversation = await chats.get_active_chat(user_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active tutor chat dialog",
        )
    return _chat_conversation_out(conversation)


@router.get(
    "/tutor/chat/{conversation_id}/messages",
    response_model=ChatHistoryOut,
)
async def get_tutor_chat_history(
    user_id: CurrentUserId,
    conversation_id: int,
    chats: ChatDep,
) -> ChatHistoryOut:
    """Messages of the caller's dialog, chronological; foreign/missing → 404."""
    conversation = await chats.get_chat(user_id, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor chat dialog not found",
        )
    messages = await chats.get_history(user_id, conversation_id)
    return ChatHistoryOut(
        conversation_id=conversation_id,
        messages=[_chat_message_out(message) for message in messages],
    )


@router.post(
    "/tutor/chat/{conversation_id}/messages",
    response_model=ChatSendOut,
)
async def send_tutor_chat_message(
    user_id: CurrentUserId,
    conversation_id: int,
    message: ChatMessageIn,
    chats: ChatDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
) -> ChatSendOut | JSONResponse:
    """Send one message to the caller's active dialog.

    Requires an ``Idempotency-Key`` header; a replay returns the saved
    reply without a new LLM call and sets ``Idempotent-Replay: true``.
    """
    result = await chats.send_message(
        user_id,
        conversation_id,
        message.text,
        client_request_id=idempotency_key,
        source="web",
    )
    if isinstance(result, ChatUnavailable):
        return _chat_error(result.reason)

    user_out = _chat_message_out(result.user_message)
    assistant_out = (
        _chat_message_out(result.assistant_message)
        if result.assistant_message is not None
        else None
    )
    if result.replay:
        response.headers["Idempotent-Replay"] = "true"
    return ChatSendOut(
        user_message=user_out,
        assistant_message=assistant_out,
        reply=result.reply,
        conversation_status="closed" if result.closed else "active",
    )


@router.post(
    "/tutor/chat/{conversation_id}/close",
    response_model=None,
)
async def close_tutor_chat(
    user_id: CurrentUserId,
    conversation_id: int,
    chats: ChatDep,
) -> dict[str, str] | JSONResponse:
    """Close the caller's active dialog; foreign/missing → 404, already
    closed → 409."""
    conversation = await chats.get_chat(user_id, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor chat dialog not found",
        )
    if conversation.status != "active":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Dialog is already closed"},
        )
    await chats.close_chat(user_id, conversation_id)
    return {"status": "closed"}


@router.delete("/tutor/chat/{conversation_id}")
async def delete_tutor_chat(
    user_id: CurrentUserId,
    conversation_id: int,
    chats: ChatDep,
) -> dict[str, str]:
    """Delete the caller's dialog with its messages; foreign/missing → 404."""
    deleted = await chats.delete_chat(user_id, conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor chat dialog not found",
        )
    return {"status": "deleted"}
