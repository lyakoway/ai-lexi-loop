"""Construction of the Bot and Dispatcher.

Pure factory functions: nothing here starts polling or performs network
calls, so importing this module (e.g. in tests) is side-effect free.
"""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import help as help_handlers
from app.bot.handlers import start as start_handlers
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
    """Create a Dispatcher wired with handlers and per-update DI."""
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(DbSessionMiddleware(session_factory))
    dispatcher.include_router(start_handlers.router)
    dispatcher.include_router(help_handlers.router)
    return dispatcher
