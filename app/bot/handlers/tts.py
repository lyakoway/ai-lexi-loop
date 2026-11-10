"""TTS playback handler: the «Послушать» button on expression cards.

Ownership: the expression must have been served to the user (progress
row) — the TTS service checks it and reports not_found for foreign
expressions without any synthesis work. The user's preferred accent and
the target language select the voice: the language profile config maps
accents to voices, falling back to the global default voice.
"""

import logging
from typing import Any

from aiogram import Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import FSInputFile

from app.bot import texts
from app.bot.handlers.navigation import resolve_user_and_profile
from app.domain.languages import get_language_profile
from app.repositories import StorageError
from app.services import ProfileService, TtsService, UserService
from app.services.tts import TtsUnavailable

logger = logging.getLogger(__name__)

router = Router(name="tts_playback")


class TtsCB(CallbackData, prefix="tts", sep="~"):
    """Listen button callback: play audio for one expression."""

    expression_id: int


@router.callback_query(TtsCB.filter())
async def on_listen(
    callback_query: Any,
    callback_data: TtsCB,
    user_service: UserService,
    profile_service: ProfileService,
    tts_service: TtsService,
) -> None:
    """Send the expression's audio as a separate voice message."""
    if callback_query.message is None or callback_query.from_user is None:
        return
    try:
        user, profile = await resolve_user_and_profile(
            callback_query.from_user.id, user_service, profile_service
        )
    except StorageError:
        logger.exception(
            "failed to load user for tts, telegram_user_id=%s",
            callback_query.from_user.id,
        )
        await callback_query.answer(texts.TTS_UNAVAILABLE, show_alert=True)
        return
    accent = profile.preferred_accent if profile else "us"
    language = profile.target_language if profile else "en"
    voice = get_language_profile(language).voice_for(accent)

    audio = await tts_service.get_or_synthesize(
        user_id=user.id,
        expression_id=callback_data.expression_id,
        voice=voice,
        accent=accent,
    )
    if isinstance(audio, TtsUnavailable):
        if audio.reason == "expression_not_found":
            await callback_query.answer(texts.CHAT_NOT_FOUND, show_alert=False)
            return
        await callback_query.answer(texts.TTS_UNAVAILABLE, show_alert=True)
        return

    await callback_query.message.answer_voice(
        FSInputFile(audio.file_path),
        caption=texts.TTS_VOICE_CAPTION,
    )
    await callback_query.answer()
