"""Construction of the Bot and Dispatcher.

Pure factory functions: nothing here starts polling or performs network
calls, so importing this module (e.g. in tests) is side-effect free.
"""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import help as help_handlers
from app.bot.handlers import lesson as lesson_handlers
from app.bot.handlers import navigation as navigation_handlers
from app.bot.handlers import onboarding as onboarding_handlers
from app.bot.handlers import review as review_handlers
from app.bot.handlers import settings as settings_handlers
from app.bot.handlers import start as start_handlers
from app.bot.handlers import stats_token as stats_token_handlers
from app.bot.middlewares.di import DbSessionMiddleware
from app.core.config import Settings
from app.db.session import SessionFactory


def create_bot(settings: Settings) -> Bot:
    """Create a Bot instance with project-wide defaults."""
    return Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(session_factory: SessionFactory) -> Dispatcher:
    """Create a Dispatcher wired with handlers and per-update DI.

    FSM state lives in the default in-memory storage: unfinished
    onboarding drafts do not survive a restart, saved profiles do.
    """
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(DbSessionMiddleware(session_factory))
    dispatcher.include_router(start_handlers.router)
    dispatcher.include_router(help_handlers.router)
    dispatcher.include_router(stats_token_handlers.router)
    dispatcher.include_router(navigation_handlers.router)
    dispatcher.include_router(lesson_handlers.router)
    dispatcher.include_router(review_handlers.router)
    dispatcher.include_router(settings_handlers.router)
    dispatcher.include_router(onboarding_handlers.router)
    return dispatcher
