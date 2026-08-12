"""The only command worth having: what am I, and what do I do with big files."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from twitter_dl.bot import texts
from twitter_dl.config import Settings

router = Router(name="start")


@router.message(CommandStart())
@router.message(Command("help"))
async def show_help(message: Message, settings: Settings) -> None:
    await message.answer(texts.HELP.format(max_mb=settings.max_tg_video_mb))
