"""Handler for /generate: topic → count → situation → preview → accept.

The flow uses the project's standard FSM patterns. The preview screen
carries a ``token`` (the first draft id of the saved set); accept and
reject callbacks compare it with the state before touching anything,
so stale presses are inert. Generation and acceptance go through the
same ``GenerationService`` the API uses.
"""

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from app.bot import texts
from app.bot.keyboards.generate import (
    GenCB,
    count_keyboard,
    preview_keyboard,
    situation_keyboard,
    topic_keyboard,
    topic_value_at,
)
from app.bot.keyboards.navigation import main_menu_keyboard
from app.bot.messages import safe_edit
from app.domain.profile import Topic
from app.repositories import StorageError
from app.services import GenerationService
from app.services.tutor import GenerationUnavailable

logger = logging.getLogger(__name__)

router = Router(name="generate")

PREVIEW_STATE = "generate:preview"


class GenerateFlow(StatesGroup):
    """States of the /generate wizard."""

    topic = State()
    count = State()
    situation = State()
    preview = State()


@router.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext) -> None:
    """Open the generation wizard."""
    await state.set_state(GenerateFlow.topic)
    await message.answer(texts.CMD_GENERATE_HINT, reply_markup=main_menu_keyboard())
    await message.answer(texts.GEN_CHOOSE_TOPIC, reply_markup=topic_keyboard())


@router.callback_query(
    GenCB.filter(F.action == "topic"), StateFilter(GenerateFlow.topic)
)
async def on_topic(
    callback: CallbackQuery, callback_data: GenCB, state: FSMContext
) -> None:
    """Remember the topic and ask for the count."""
    topic = topic_value_at(callback_data.token)
    await state.update_data(gen_topic=topic)
    await state.set_state(GenerateFlow.count)
    await callback.answer()
    await safe_edit(callback.message, texts.GEN_CHOOSE_COUNT, count_keyboard())


@router.callback_query(
    GenCB.filter(F.action == "count"), StateFilter(GenerateFlow.count)
)
async def on_count(
    callback: CallbackQuery, callback_data: GenCB, state: FSMContext
) -> None:
    """Remember the count and ask for the optional situation."""
    await state.update_data(gen_count=callback_data.token)
    await state.set_state(GenerateFlow.situation)
    await callback.answer()
    await safe_edit(
        callback.message,
        f"{texts.GEN_CHOOSE_SITUATION}\n\n<i>{texts.GEN_SITUATION_HINT}</i>",
        situation_keyboard(),
    )


@router.message(StateFilter(GenerateFlow.situation), F.text)
async def on_situation_text(
    message: Message, state: FSMContext, generation_service: GenerationService
) -> None:
    """Take the situation text and run the generation."""
    await state.update_data(gen_situation=(message.text or "")[:300])
    await message.answer(texts.GEN_GENERATING)
    await _generate(message, state, generation_service)


@router.callback_query(
    GenCB.filter(F.action == "skip_situation"), StateFilter(GenerateFlow.situation)
)
async def on_situation_skip(
    callback: CallbackQuery, state: FSMContext, generation_service: GenerationService
) -> None:
    """Run the generation without a situation."""
    await state.update_data(gen_situation="")
    await callback.answer(texts.GEN_GENERATING)
    await _generate(callback.message, state, generation_service)


async def _generate(
    message: Message | InaccessibleMessage | None,
    state: FSMContext,
    generation_service: GenerationService,
) -> None:
    """Run generation and render the preview screen."""
    data = await state.get_data()
    topic: str = data.get("gen_topic", "any")
    count: int = int(data.get("gen_count", 5))
    situation: str = data.get("gen_situation", "")
    if topic == "any":
        # Wildcard: rotate over the profile topics deterministically.
        topics = [t.value for t in Topic]
        topic = topics[len(situation) % len(topics)]
    user_id = data.get("user_id")
    if user_id is None:
        await state.clear()
        if message is not None:
            await message.answer("Профиль не найден, начните с /start")
        return

    try:
        result = await generation_service.generate_drafts(
            int(user_id), topic=topic, count=count, situation=situation
        )
    except StorageError:
        logger.exception("generation storage failure, user_id=%s", user_id)
        await state.clear()
        if message is not None:
            await message.answer(
                texts.GEN_UNAVAILABLE, reply_markup=main_menu_keyboard()
            )
        return

    if isinstance(result, GenerationUnavailable):
        await state.clear()
        if message is not None:
            await message.answer(
                texts.GEN_UNAVAILABLE, reply_markup=main_menu_keyboard()
            )
        return

    if not result.drafts:
        await state.clear()
        if message is not None:
            await message.answer(
                texts.GEN_NONE_SAVED, reply_markup=main_menu_keyboard()
            )
        return

    draft_ids = [draft.id for draft in result.drafts]
    await state.set_state(GenerateFlow.preview)
    await state.update_data(gen_token=draft_ids[0], gen_draft_ids=draft_ids)
    lines = "\n".join(
        texts.GEN_PREVIEW_ITEM.format(
            number=number, text=draft.text, translation=draft.translation
        )
        for number, draft in enumerate(result.drafts, start=1)
    )
    note = (
        f"\n\n<i>Тема: {topic}"
        + (
            f" · отброшены дубликаты: {result.duplicates_skipped}"
            if result.duplicates_skipped
            else ""
        )
        + "</i>"
    )
    if message is not None:
        await message.answer(
            f"{texts.GEN_PREVIEW_TITLE}\n\n{lines}{note}",
            reply_markup=preview_keyboard(token=draft_ids[0]),
        )


async def _load_preview(
    callback: CallbackQuery, callback_data: GenCB, state: FSMContext
) -> dict[str, Any] | None:
    """Validate an accept/reject press against the preview state."""
    if await state.get_state() != GenerateFlow.preview.state:
        return None
    data = await state.get_data()
    if callback_data.token != data.get("gen_token"):
        return None
    return data


@router.callback_query(
    GenCB.filter(F.action == "accept"), StateFilter(GenerateFlow.preview)
)
async def on_accept(
    callback: CallbackQuery,
    callback_data: GenCB,
    state: FSMContext,
    generation_service: GenerationService,
) -> None:
    """Accept the whole previewed set into learning."""
    data = await _load_preview(callback, callback_data, state)
    if data is None:
        await callback.answer(texts.GEN_STALE, show_alert=False)
        return
    draft_ids: list[int] = list(data["gen_draft_ids"])
    user_id = int(data["user_id"])
    try:
        result = await generation_service.accept_drafts(user_id, draft_ids)
    except StorageError:
        logger.exception("accept storage failure, user_id=%s", user_id)
        await callback.answer(texts.GEN_UNAVAILABLE, show_alert=True)
        return
    await state.clear()
    await callback.answer("Готово")
    await safe_edit(
        callback.message,
        texts.GEN_ACCEPTED_DONE.format(accepted=len(result.accepted)),
        main_menu_keyboard(),
    )


@router.callback_query(
    GenCB.filter(F.action == "reject"), StateFilter(GenerateFlow.preview)
)
async def on_reject(
    callback: CallbackQuery,
    callback_data: GenCB,
    state: FSMContext,
    generation_service: GenerationService,
) -> None:
    """Reject the previewed set; it never enters lessons."""
    data = await _load_preview(callback, callback_data, state)
    if data is None:
        await callback.answer(texts.GEN_STALE, show_alert=False)
        return
    draft_ids: list[int] = list(data["gen_draft_ids"])
    user_id = int(data["user_id"])
    try:
        await generation_service.reject_drafts(user_id, draft_ids)
    except StorageError:
        logger.exception("reject storage failure, user_id=%s", user_id)
        await callback.answer(texts.GEN_UNAVAILABLE, show_alert=True)
        return
    await state.clear()
    await callback.answer("Готово")
    await safe_edit(callback.message, texts.GEN_REJECTED_DONE, main_menu_keyboard())


@router.callback_query(GenCB.filter())
async def on_stale_generate_callback(callback: CallbackQuery) -> None:
    """Answer generation buttons when no wizard is active."""
    await callback.answer(texts.GEN_STALE, show_alert=False)
