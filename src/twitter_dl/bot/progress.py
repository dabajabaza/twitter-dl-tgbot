"""The single status message that follows a request from queue to result.

One message per request, edited in place, so a busy chat stays readable. Edits
are throttled: yt-dlp reports progress many times a second, while Telegram
starts rejecting edits to the same message well below that rate.

Two properties this class owes the rest of the bot:

* **A verdict is final.** Once the request is over, nothing may overwrite what
  the user was told — including a progress callback from a download thread that
  is still unwinding (see services/downloader.py).
* **It never raises.** A status message that cannot be updated is cosmetic
  damage; letting it propagate would take down the only queue consumer.
"""

import asyncio
import contextlib
import logging
from time import monotonic

from aiogram import Bot

logger = logging.getLogger(__name__)

_MIN_EDIT_INTERVAL_S = 5.0
_SENT_FALLBACK = "Sent."


class ProgressReporter:
    """Owns one status message, the pace at which it changes, and its last word."""

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
        self._closed = False
        # One edit at a time: without it a throttled flush and a state change
        # can be in flight against the same message_id simultaneously, and the
        # order they land in is then Telegram's to decide, not ours.
        self._edit_lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    async def set(self, text: str) -> None:
        """Show ``text`` now — for state changes, which are rare and worth seeing."""
        if self._closed:
            return
        self._pending = None
        await self._edit(text)

    def offer(self, text: str) -> None:
        """Offer ``text`` for display when the throttle allows.

        Synchronous and fire-and-forget, so the download loop never waits on
        Telegram. Must be called on the event loop thread.
        """
        if self._closed or text == self._shown:
            return
        self._pending = text
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._flush())

    async def finish(self, text: str) -> None:
        """Leave ``text`` as the request's last word and stop updating for good."""
        if self._closed:
            return
        await self.close()
        await self._edit(text)

    async def replace_with_upload(self) -> None:
        """Drop the status message, because the video itself now stands in its place."""
        if self._closed:
            return
        await self.close()
        try:
            await self._bot.delete_message(chat_id=self._chat_id, message_id=self._message_id)
        except Exception as exc:
            # Deletion is cosmetic; a bot may not delete messages older than 48
            # hours, and a slow enough download can reach that.
            logger.debug("could not delete status message: %s", exc)
            await self._edit(_SENT_FALLBACK)

    async def close(self) -> None:
        """Stop updating this message, for good.

        Terminal on purpose: a download thread abandoned on timeout keeps
        calling back for a while, and without this its stale percentage would
        reappear on top of the verdict the user was just given.
        """
        self._closed = True
        self._pending = None
        flusher, self._flusher = self._flusher, None
        if flusher is not None and not flusher.done():
            flusher.cancel()
            # Cancelling our own helper is expected; the request itself is not
            # being cancelled, so this must not propagate.
            with contextlib.suppress(asyncio.CancelledError):
                await flusher

    async def _flush(self) -> None:
        while self._pending is not None and not self._closed:
            wait = self._min_interval - (monotonic() - self._last_edit)
            if wait > 0:
                await asyncio.sleep(wait)
            text, self._pending = self._pending, None
            # `set()` may have cleared the pending text while we slept — its
            # state change outranks whatever progress we were about to show.
            if text is not None and not self._closed:
                await self._edit(text)

    async def _edit(self, text: str) -> None:
        if text == self._shown:
            return
        async with self._edit_lock:
            if text == self._shown:
                return
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id, message_id=self._message_id, text=text
                )
            except Exception as exc:
                # Deliberately every exception, not just TelegramAPIError:
                # aiogram raises ClientDecodeError (an AiogramError, *not* a
                # TelegramAPIError) whenever Telegram's front end answers with
                # an HTML error page instead of JSON. Letting that escape would
                # kill the queue consumer over a transient 502.
                logger.warning("status edit failed: %s: %s", type(exc).__name__, exc)
            else:
                self._shown = text
            finally:
                self._last_edit = monotonic()
