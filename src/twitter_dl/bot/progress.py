"""The single status message that follows a request from queue to result.

One message per request, edited in place, so a busy chat stays readable. Edits
are throttled: yt-dlp reports progress many times a second, while Telegram
starts rejecting edits to the same message well below that rate.
"""

import asyncio
import contextlib
import logging
from time import monotonic

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

logger = logging.getLogger(__name__)

_MIN_EDIT_INTERVAL_S = 5.0


class ProgressReporter:
    """Owns one status message and the pace at which it changes."""

    def __init__(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        min_interval: float = _MIN_EDIT_INTERVAL_S,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id
        self._min_interval = min_interval
        self._shown = ""
        self._pending: str | None = None
        self._last_edit = float("-inf")
        self._flusher: asyncio.Task[None] | None = None

    async def set(self, text: str) -> None:
        """Show ``text`` now — for state changes, which are rare and worth seeing."""
        self._pending = None
        await self._edit(text)

    def offer(self, text: str) -> None:
        """Offer ``text`` for display when the throttle allows.

        Synchronous and fire-and-forget, so the download loop never waits on
        Telegram. Must be called on the event loop thread.
        """
        if text == self._shown:
            return
        self._pending = text
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._flush())

    async def finish(self, text: str) -> None:
        """Leave ``text`` as the request's last word and stop updating."""
        await self.close()
        await self._edit(text)

    async def replace_with_upload(self) -> None:
        """Drop the status message, because the video itself now stands in its place."""
        await self.close()
        try:
            await self._bot.delete_message(chat_id=self._chat_id, message_id=self._message_id)
        except TelegramAPIError as exc:
            # Deletion is cosmetic; a bot may not delete messages older than 48
            # hours, and a slow enough download can reach that.
            logger.debug("could not delete status message: %s", exc)
            await self._edit(_SENT_FALLBACK)

    async def close(self) -> None:
        """Stop the throttled updates, waiting for one in flight to land."""
        self._pending = None
        flusher, self._flusher = self._flusher, None
        if flusher is not None and not flusher.done():
            flusher.cancel()
            # Cancelling our own helper is expected; the request itself is not
            # being cancelled, so this must not propagate.
            with contextlib.suppress(asyncio.CancelledError):
                await flusher

    async def _flush(self) -> None:
        while self._pending is not None:
            wait = self._min_interval - (monotonic() - self._last_edit)
            if wait > 0:
                await asyncio.sleep(wait)
            text, self._pending = self._pending, None
            # `set()` may have cleared the pending text while we slept — its
            # state change outranks whatever progress we were about to show.
            if text is not None:
                await self._edit(text)

    async def _edit(self, text: str) -> None:
        if text == self._shown:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id, message_id=self._message_id, text=text
            )
        except TelegramBadRequest as exc:
            logger.debug("status edit rejected: %s", exc)
        except TelegramAPIError as exc:
            # A status message that cannot be updated is not worth failing a
            # download over — the clip still arrives.
            logger.warning("status edit failed: %s", exc)
        else:
            self._shown = text
        finally:
            self._last_edit = monotonic()


_SENT_FALLBACK = "Sent."
