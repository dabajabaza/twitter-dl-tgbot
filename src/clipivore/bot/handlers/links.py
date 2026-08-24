"""Accepting post links. There is no command to remember — a link is the command."""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Filter
from aiogram.types import Message

from clipivore.bot import texts
from clipivore.bot.progress import ProgressReporter
from clipivore.config import Settings
from clipivore.runtime.worker import Request, RequestQueue, SourceMessage
from clipivore.services.overflow import OverflowCatalog
from clipivore.services.providers import ClaimedLink, ProviderCatalog

logger = logging.getLogger(__name__)

router = Router(name="links")


class HasPostLinks(Filter):
    """Matches a message carrying at least one link some Provider claims.

    Returning a dict merges it into the handler's arguments, so the links are
    extracted once here rather than again inside the handler. The catalog is
    declared as a parameter, which aiogram fills from the dispatcher's workflow
    data — the same singleton the handlers get.
    """

    async def __call__(
        self, message: Message, provider_catalog: ProviderCatalog
    ) -> bool | dict[str, Any]:
        links = provider_catalog.extract(message.text, message.caption, *_hidden_urls(message))
        return {"links": links} if links else False


def _hidden_urls(message: Message) -> Iterator[str]:
    """URLs that live in formatting rather than in the text a person can see.

    Handed to the catalog as their own source, so they queue after the visible
    links rather than in the position they were written at. Entities do carry an
    offset, so interleaving them properly is possible — it has just never been
    worth it for a message holding one of each.
    """
    for entity in (*(message.entities or ()), *(message.caption_entities or ())):
        if entity.type == "text_link" and entity.url:
            yield entity.url


@router.message(HasPostLinks())
async def enqueue_links(
    message: Message,
    links: list[ClaimedLink],
    bot: Bot,
    queue: RequestQueue,
    settings: Settings,
    overflow_catalog: OverflowCatalog,
) -> None:
    user = message.from_user
    if user is None:
        return

    # Created before the first await: the count must be settled while no
    # request can possibly have finished yet.
    source = SourceMessage(
        bot, chat_id=message.chat.id, message_id=message.message_id, expected=len(links)
    )
    for link in links:
        if not link.choice.ready:
            # Claimed by a Provider that could not be built. Refusing here
            # rather than in the worker keeps a broken platform from spending a
            # queue slot, and names it instead of ignoring the link. The verdict
            # is known before any await, so it is said once — no "Queued…" that
            # exists only to be edited away a moment later.
            logger.info("refused %s: provider %s is broken", link.url, link.choice.provider_id)
            source.abandon()
            await message.answer(texts.PROVIDER_MISCONFIGURED.format(provider=link.choice.name))
            continue
        status = await message.answer(texts.QUEUED)
        reporter = ProgressReporter(bot, chat_id=status.chat.id, message_id=status.message_id)
        request = Request(
            url=link.url,
            chat_id=message.chat.id,
            user_id=user.id,
            reporter=reporter,
            overflow=overflow_catalog.current,
            provider=link.choice,
            source=source,
        )
        try:
            position = queue.submit(request)
        except asyncio.QueueFull:
            # The refused link and the skipped ones never become Requests, so
            # the message must survive however the accepted ones end.
            source.abandon()
            await reporter.finish(texts.QUEUE_FULL.format(limit=settings.queue_limit))
            # Every remaining link in this message would meet the same full
            # queue, and repeating the refusal per link is just noise.
            return

        logger.info("queued %s for %s at position %s", link.url, user.id, position)
        if position > 1:
            await reporter.set(texts.QUEUED_POSITION.format(position=position))
