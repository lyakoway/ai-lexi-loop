"""Handlers for /stats (learning statistics) and /token (Web API access).

Both commands work for any registered user; /stats additionally shows
zeros when there is nothing to report yet, while /token issues a real
personal bearer token for the HTTP API.
"""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts
from app.bot.keyboards.navigation import back_to_menu_keyboard
from app.bot.stats_view import format_overview
from app.repositories import StorageError
from app.services import (
    ApiTokenService,
    StatsService,
    TokenLimitError,
    UserService,
)
from app.services.tokens import MAX_TOKENS_PER_USER

logger = logging.getLogger(__name__)

router = Router(name="stats_token")


@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    user_service: UserService,
    stats_service: StatsService,
) -> None:
    """Show the user's learning statistics."""
    if message.from_user is None:
        return
    try:
        user, _ = await user_service.ensure_registered(message.from_user.id)
        overview = await stats_service.get_overview(user.id)
    except StorageError:
        logger.exception(
            "failed to load stats, telegram_user_id=%s", message.from_user.id
        )
        await message.answer(
            "Не удалось загрузить статистику — сервис временно недоступен."
        )
        return
    await message.answer(
        format_overview(overview), reply_markup=back_to_menu_keyboard()
    )


@router.message(Command("token"))
async def cmd_token(
    message: Message,
    user_service: UserService,
    token_service: ApiTokenService,
) -> None:
    """Issue a personal API token and show it exactly once."""
    if message.from_user is None:
        return
    try:
        user, _ = await user_service.ensure_registered(message.from_user.id)
        issued = await token_service.issue(user.id)
    except TokenLimitError:
        await message.answer(
            texts.TOKEN_LIMIT_REACHED.format(limit=MAX_TOKENS_PER_USER)
        )
        return
    except StorageError:
        logger.exception(
            "failed to issue token, telegram_user_id=%s", message.from_user.id
        )
        await message.answer(texts.TOKEN_ISSUE_FAILED)
        return
    logger.info("issued api token id=%s for user_id=%s", issued.id, user.id)
    await message.answer(
        f"{texts.TOKEN_TITLE}\\n\\n"
        f"{texts.TOKEN_VALUE.format(token=issued.token)}\\n\\n"
        f"{texts.TOKEN_INSTRUCTIONS}"
    )
