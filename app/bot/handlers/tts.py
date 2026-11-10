"""TTS playback handler: the «Послушать» button on expression cards.

Ownership: the expression must have been served to the user (progress
row) — the TTS service checks it and reports not_found for foreign
expressions without any synthesis work. The user's preferred accent
comes from their profile.
"""

import logging
from typing import Any

from aiogram import Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import FSInputFile

from app.bot import texts
from app.services.tts import TtsService, TtsUnavailable

logger = logging.getLogger(__name__)

router = Router(name="tts_playback")


class TtsCB(CallbackData, prefix="tts", sep="~"):
    """Listen button callback: play audio for one expression."""

    expression_id: int


@router.callback_query(TtsCB.filter())
async def on_listen(
    callback_query: Any,
    callback_data: TtsCB,
    tts_service: TtsService,
    profile_service: Any,
) -> None:
    """Send the expression's audio as a separate voice message."""
    if callback_query.message is None or callback_query.from_user is None:
        return
    profile = await profile_service.get_by_user_id(callback_query.from_user.id)
    accent = profile.preferred_accent if profile else "us"

    audio = await tts_service.get_or_synthesize(
        user_id=callback_query.from_user.id,
        expression_id=callback_data.expression_id,
        voice=tts_service.default_voice,
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
