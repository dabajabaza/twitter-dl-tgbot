"""Owner-only Menu for selecting the bot's Overflow destination."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from twitter_dl.bot import texts
from twitter_dl.config import Settings
from twitter_dl.services.overflow import OverflowCatalog

logger = logging.getLogger(__name__)
router = Router(name="overflow")

_CALLBACK_PREFIX = "overflow:"


def _keyboard(catalog: OverflowCatalog) -> InlineKeyboardMarkup:
    current_id = catalog.current.adapter_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        f"{texts.OVERFLOW_CURRENT_MARKER}{texts.overflow_label(choice)}"
                        if choice.adapter_id == current_id
                        else texts.overflow_label(choice)
                    ),
                    callback_data=f"{_CALLBACK_PREFIX}{choice.adapter_id}",
                )
            ]
            for choice in catalog.selectable
        ]
    )


@router.message(Command("overflow"))
async def show_overflow_menu(
    message: Message,
    settings: Settings,
    overflow_catalog: OverflowCatalog,
) -> None:
    user = message.from_user
    if user is None or user.id != settings.owner_id:
        return
    await message.answer(
        texts.overflow_menu(overflow_catalog),
        reply_markup=_keyboard(overflow_catalog),
    )


@router.callback_query(F.data.startswith(_CALLBACK_PREFIX))
async def select_overflow(
    callback: CallbackQuery,
    settings: Settings,
    overflow_catalog: OverflowCatalog,
) -> None:
    if callback.from_user.id != settings.owner_id:
        return

    adapter_id = (callback.data or "").removeprefix(_CALLBACK_PREFIX)
    current = overflow_catalog.current
    unchanged = adapter_id == current.adapter_id and current in overflow_catalog.selectable
    try:
        selected = overflow_catalog.select(adapter_id)
    except ValueError:
        await callback.answer(texts.OVERFLOW_NOT_SELECTABLE, show_alert=True)
        return
    except OSError as exc:
        logger.error("could not save Overflow selection: %s", exc)
        await callback.answer(texts.OVERFLOW_SAVE_FAILED, show_alert=True)
        return

    await callback.answer(texts.OVERFLOW_SELECTED.format(adapter=texts.overflow_label(selected)))
    if unchanged:
        return
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                texts.overflow_menu(overflow_catalog),
                reply_markup=_keyboard(overflow_catalog),
            )
        except Exception as exc:
            logger.warning("Overflow selection saved but Menu refresh failed: %s", exc)
