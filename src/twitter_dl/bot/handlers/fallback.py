"""Anything from a whitelisted user that held no tweet link.

Registered last, so it only ever sees what nothing else wanted.
"""

from aiogram import Router
from aiogram.types import Message

from twitter_dl.bot import texts

router = Router(name="fallback")


@router.message()
async def no_link_found(message: Message) -> None:
    await message.answer(texts.NO_LINK)
