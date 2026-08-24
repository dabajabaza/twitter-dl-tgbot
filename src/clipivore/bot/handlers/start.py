"""The help text shared by /start and /help."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from clipivore.bot import texts
from clipivore.config import Settings
from clipivore.services.overflow import OverflowCatalog

router = Router(name="start")


@router.message(CommandStart())
@router.message(Command("help"))
async def show_help(
    message: Message, settings: Settings, overflow_catalog: OverflowCatalog
) -> None:
    await message.answer(texts.help_message(settings.max_tg_video_mb, overflow_catalog.current))
