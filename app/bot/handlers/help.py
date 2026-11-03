"""Handler for ``/help``."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts

router = Router(name="help")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Show what the trainer can do."""
    await message.answer(texts.HELP_TEXT)
