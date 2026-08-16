"""Accepting tweet links. There is no command to remember — a link is the command."""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Filter
from aiogram.types import Message

from twitter_dl.bot import texts
from twitter_dl.bot.progress import ProgressReporter
from twitter_dl.config import Settings
from twitter_dl.runtime.worker import Request, RequestQueue
from twitter_dl.services.links import extract_links
from twitter_dl.services.overflow import OverflowCatalog

logger = logging.getLogger(__name__)

router = Router(name="links")


class HasTweetLinks(Filter):
    """Matches a message carrying at least one tweet link, and hands them over.

    Returning a dict merges it into the handler's arguments, so the links are
    extracted once here rather than again inside the handler.
    """

    async def __call__(self, message: Message) -> bool | dict[str, Any]:
        urls = extract_links(message.text, message.caption, *_hidden_urls(message))
        return {"urls": urls} if urls else False


def _hidden_urls(message: Message) -> Iterator[str]:
    """URLs that live in formatting rather than in the text a person can see."""
    for entity in (*(message.entities or ()), *(message.caption_entities or ())):
        if entity.type == "text_link" and entity.url:
            yield entity.url


@router.message(HasTweetLinks())
async def enqueue_links(
    message: Message,
    urls: list[str],
    bot: Bot,
    queue: RequestQueue,
    settings: Settings,
    overflow_catalog: OverflowCatalog,
) -> None:
    user = message.from_user
    if user is None:
        return

    for url in urls:
        status = await message.answer(texts.QUEUED)
        reporter = ProgressReporter(bot, chat_id=status.chat.id, message_id=status.message_id)
        request = Request(
            url=url,
            chat_id=message.chat.id,
            user_id=user.id,
            reporter=reporter,
            overflow=overflow_catalog.current,
        )
        try:
            position = queue.submit(request)
        except asyncio.QueueFull:
            await reporter.finish(texts.QUEUE_FULL.format(limit=settings.queue_limit))
            # Every remaining link in this message would meet the same full
            # queue, and repeating the refusal per link is just noise.
            return

        logger.info("queued %s for %s at position %s", url, user.id, position)
        if position > 1:
            await reporter.set(texts.QUEUED_POSITION.format(position=position))
