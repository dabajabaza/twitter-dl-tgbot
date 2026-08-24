"""The queue and the single worker that drains it.

One request at a time, deliberately. The uplink is one proxied tunnel and the
server is an old laptop, so running downloads in parallel would not finish any
of them sooner — it would only make progress percentages and timeouts lie
(see docs/ARCHITECTURE.md D8).
"""

import asyncio
import html
import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aiogram import Bot
from aiogram.enums import ParseMode

from twitter_dl.bot import texts
from twitter_dl.bot.captions import build_caption
from twitter_dl.bot.progress import ProgressReporter
from twitter_dl.config import Settings
from twitter_dl.domain import Clip, ProgressCallback
from twitter_dl.errors import (
    AuthExpired,
    DownloadFailed,
    DownloadTooLarge,
    NetworkUnavailable,
    NotATweetLink,
    NoVideoInTweet,
    OverflowFailed,
    OverflowUnavailable,
    TweetUnavailable,
    TwitterDlError,
)
from twitter_dl.services.cookies import CookieSession
from twitter_dl.services.delivery import DeliveryResult, OverflowDelivery
from twitter_dl.services.links import is_short_link, resolve_short_link
from twitter_dl.services.overflow import OverflowChoice

logger = logging.getLogger(__name__)

_REPLY_FOR: dict[type[TwitterDlError], str] = {
    NotATweetLink: texts.NOT_A_TWEET,
    NoVideoInTweet: texts.NO_VIDEO,
    TweetUnavailable: texts.TWEET_UNAVAILABLE,
    NetworkUnavailable: texts.NETWORK_UNAVAILABLE,
    DownloadFailed: texts.DOWNLOAD_FAILED,
}


class Downloader(Protocol):
    """What the worker needs from a download engine — stated where it is used,
    so the worker never has to import yt-dlp to be exercised."""

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        on_progress: ProgressCallback | None = None,
        max_bytes: int | None = None,
    ) -> list[Clip]: ...


class Delivery(Protocol):
    """What the worker needs from a delivery route."""

    async def deliver(
        self,
        clip: Clip,
        *,
        chat_id: int,
        caption: str,
        overflow: OverflowChoice,
        index: int = 1,
        total: int = 1,
    ) -> DeliveryResult: ...


class SourceMessage:
    """The user's message a batch of Requests was spawned from.

    Deleted only when every Request from it succeeded — one link that failed
    means the person still needs the message to retry. ``expected`` is fixed
    synchronously in the handler before its first await, so a request that
    finishes while later links are still being enqueued cannot see a zero
    pending count and delete the message early.
    """

    def __init__(self, bot: Bot, *, chat_id: int, message_id: int, expected: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id
        self._pending = expected
        self._failed = False

    def abandon(self) -> None:
        """Some links never became Requests (full queue): the message survives."""
        self._failed = True

    async def resolve(self, *, succeeded: bool) -> None:
        """One Request's terminal outcome.

        Never raises, and the failure path never suspends: it runs from a
        ``finally`` that may be unwinding a CancelledError.
        """
        self._pending -= 1
        if not succeeded:
            self._failed = True
        if self._pending == 0 and not self._failed:
            try:
                await self._bot.delete_message(chat_id=self._chat_id, message_id=self._message_id)
            except Exception as exc:
                # Cosmetic, like the status-message delete: a bot may not
                # delete messages older than 48 hours.
                logger.debug("could not delete source message %s: %s", self._message_id, exc)


@dataclass
class Request:
    """One tweet link accepted from one user, holding one queue slot."""

    url: str
    chat_id: int
    user_id: int
    reporter: ProgressReporter
    overflow: OverflowChoice
    source: SourceMessage | None = None


class RequestQueue:
    """Bounded work list with a single consumer.

    The bound counts the request being worked on, not just those waiting: five
    means five in the system, which is what a person forwarding a channel post
    full of links would otherwise blow past.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._waiting: asyncio.Queue[Request] = asyncio.Queue()
        self._in_flight = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def load(self) -> int:
        """Requests in the system: waiting plus the one being worked on."""
        return self._waiting.qsize() + self._in_flight

    def submit(self, request: Request) -> int:
        """Accept ``request`` and return its position in line (1 means next up).

        Raises `asyncio.QueueFull` when the bot is already at its limit.
        """
        if self.load >= self._limit:
            raise asyncio.QueueFull
        self._waiting.put_nowait(request)
        return self.load

    async def take(self) -> Request:
        """Block until there is work, then mark it as being worked on."""
        request = await self._waiting.get()
        self._in_flight += 1
        return request

    def release(self) -> None:
        """Report the current request as finished, however it ended."""
        self._in_flight = max(0, self._in_flight - 1)
        self._waiting.task_done()


class OwnerAlerts:
    """Tells the owner about the one failure only they can fix.

    Deduplicated by the identity of the owner's cookie *export* — which the bot
    never writes to (see services/cookies.py) — so the owner hears once per
    export, and replacing the file re-arms the alert. Counting successes instead
    would go quiet at the wrong moment: public tweets keep downloading with a
    dead session, which is precisely why the breakage is easy to miss.
    """

    def __init__(self, bot: Bot, *, owner_id: int, cookies: CookieSession | None) -> None:
        self._bot = bot
        self._owner_id = owner_id
        self._cookies = cookies
        self._alerted = False
        self._alerted_version: tuple[float, int] | None = None

    async def auth_expired(self, detail: str) -> None:
        version = self._cookies.version() if self._cookies else None
        # `None` means "cannot tell which export this is" — the file is being
        # replaced right now, or stat failed. That is not evidence of a new
        # session, so it must not re-arm the alert: the owner mid-swap would
        # otherwise get a duplicate, and the remembered version would be
        # clobbered with None.
        if self._alerted and (version is None or self._alerted_version == version):
            return
        try:
            await self._bot.send_message(
                chat_id=self._owner_id,
                text=texts.OWNER_AUTH_EXPIRED.format(path=self._cookies_path(), detail=detail),
            )
        except Exception as exc:
            # The alert is not marked as delivered, so the next private tweet
            # tries again. Marking first and sending second would lose the one
            # signal this bot owes the owner to a single flap of the proxy.
            logger.warning("could not alert the owner: %s: %s", type(exc).__name__, exc)
            return
        self._alerted = True
        self._alerted_version = version

    def _cookies_path(self) -> str:
        source = self._cookies.source if self._cookies else None
        return str(source) if source else "COOKIES_FILE"


class RequestWorker:
    """Drains the queue, one request at a time, and narrates it into the chat."""

    def __init__(
        self,
        *,
        queue: RequestQueue,
        downloader: Downloader,
        delivery: Delivery,
        alerts: OwnerAlerts,
        settings: Settings,
    ) -> None:
        self._queue = queue
        self._downloader = downloader
        self._delivery = delivery
        self._alerts = alerts
        self._settings = settings

    async def run(self) -> None:
        while True:
            request = await self._queue.take()
            try:
                await self._process(request)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The worker is the only consumer: one unhandled exception here
                # would leave every later request queued forever.
                logger.exception("request for %s failed unexpectedly", request.url)
                await _say(request, texts.DOWNLOAD_FAILED)
            finally:
                self._queue.release()

    async def _process(self, request: Request) -> None:
        scratch = Path(tempfile.mkdtemp(dir=self._settings.download_dir, prefix="req-"))
        succeeded = False
        try:
            async with asyncio.timeout(self._settings.download_timeout_s):
                await request.reporter.set(texts.DOWNLOADING)
                url = await self._resolve(request.url)
                clips = await self._downloader.download(
                    url,
                    scratch,
                    on_progress=_progress_into(request.reporter),
                    max_bytes=(
                        None if request.overflow.ready else self._settings.max_tg_video_bytes
                    ),
                )
                overflows = await self._deliver(request, url, clips)
            # Outside the deadline on purpose. It exists to bound the *work*,
            # and by here the work is done — the clips are delivered. Leaving
            # the verdict inside meant a deadline expiring during that last
            # edit cancelled the edit itself, freezing the status message on
            # "Uploading…" forever. On an Overflow route that would lose the
            # only locator returned by the Adapter (D7).
            await self._announce(request, url=url, clips=clips, overflows=overflows)
            succeeded = True
        except TimeoutError:
            minutes = self._settings.download_timeout_s // 60
            logger.warning("request for %s timed out after %s min", request.url, minutes)
            await _say(request, texts.TIMED_OUT.format(minutes=minutes))
        except AuthExpired as exc:
            logger.warning("X rejected the stored cookies: %s", exc)
            await self._alerts.auth_expired(str(exc))
            await _say(request, texts.AUTH_EXPIRED)
        except DownloadTooLarge:
            await _say(
                request,
                texts.overflow_unavailable(
                    request.overflow,
                    max_mb=self._settings.max_tg_video_mb,
                ),
            )
        except OverflowUnavailable as exc:
            logger.info("request for %s has no Overflow delivery: %s", request.url, exc)
            await _say(
                request,
                texts.overflow_unavailable(
                    request.overflow,
                    max_mb=self._settings.max_tg_video_mb,
                ),
            )
        except OverflowFailed as exc:
            logger.info("Overflow delivery for %s failed: %s", request.url, exc)
            await _say(
                request,
                texts.OVERFLOW_FAILED.format(adapter=request.overflow.label),
            )
        except TwitterDlError as exc:
            logger.info("request for %s failed: %s: %s", request.url, type(exc).__name__, exc)
            await _say(request, _REPLY_FOR.get(type(exc), texts.DOWNLOAD_FAILED))
        finally:
            # The scratch directory holds the whole clip, so leaving it behind
            # would fill the jail's dataset a few requests later.
            shutil.rmtree(scratch, ignore_errors=True)
            # Every way out of this method lands here exactly once — the typed
            # verdicts above, a TimeoutError, an unexpected exception on its
            # way to run()'s catch-all, and cancellation — so this is the one
            # place the source message learns how its request ended.
            if request.source is not None:
                await request.source.resolve(succeeded=succeeded)

    async def _resolve(self, url: str) -> str:
        if not is_short_link(url):
            return url
        return await resolve_short_link(url, proxy=self._settings.ytdlp_proxy)

    async def _deliver(
        self, request: Request, url: str, clips: list[Clip]
    ) -> list[OverflowDelivery]:
        """Send every clip where it belongs; return external delivery receipts."""
        if not request.overflow.ready and any(
            clip.path.stat().st_size > self._settings.max_tg_video_bytes for clip in clips
        ):
            raise OverflowUnavailable(
                adapter_id=request.overflow.adapter_id,
                state=request.overflow.state.value,
            )
        total = len(clips)
        overflows: list[OverflowDelivery] = []
        for index, clip in enumerate(clips, start=1):
            await request.reporter.set(
                _delivery_status(clip, request.overflow, self._settings, index, total)
            )
            result = await self._delivery.deliver(
                clip,
                chat_id=request.chat_id,
                caption=build_caption(
                    clip.description,
                    url,
                    uploader=clip.uploader,
                    uploader_url=clip.uploader_url,
                ),
                overflow=request.overflow,
                index=index,
                total=total,
            )
            if isinstance(result, OverflowDelivery):
                overflows.append(result)
        return overflows

    async def _announce(
        self,
        request: Request,
        *,
        url: str,
        clips: list[Clip],
        overflows: list[OverflowDelivery],
    ) -> None:
        """The request's last word, once the clips are already delivered."""
        if not overflows:
            # Every clip made it into the chat, so the status message has
            # nothing left to say and the videos speak for themselves.
            await request.reporter.replace_with_upload()
            return
        blocks = [
            # The locator and label are foreign text; under an HTML parse mode
            # they must not be able to parse as markup.
            html.escape(
                texts.OVERFLOW_RESULT.format(
                    size=texts.human_size(overflow.size_bytes),
                    adapter=overflow.adapter_label,
                    location=overflow.location,
                )
            )
            for overflow in overflows
        ]
        # The verdict carries the tweet's text and links itself: the source
        # message is deleted on success, so this is where they survive.
        first = clips[0] if clips else None
        tail = build_caption(
            first.description if first else "",
            url,
            uploader=first.uploader if first else "",
            uploader_url=first.uploader_url if first else "",
        )
        await request.reporter.finish(
            "\n\n".join([*blocks, tail]),
            parse_mode=ParseMode.HTML,
        )


def _delivery_status(
    clip: Clip,
    overflow: OverflowChoice,
    settings: Settings,
    index: int,
    total: int,
) -> str:
    if clip.path.stat().st_size > settings.max_tg_video_bytes:
        return texts.DELIVERING_OVERFLOW.format(adapter=overflow.label)
    if total > 1:
        return texts.UPLOADING_MANY.format(index=index, total=total)
    return texts.UPLOADING


def _progress_into(reporter: ProgressReporter) -> Callable[[str], None]:
    def report(progress: str) -> None:
        reporter.offer(texts.DOWNLOADING_PROGRESS.format(progress=progress))

    return report


async def _say(request: Request, text: str) -> None:
    """Deliver a verdict. Never raises.

    This is the last thing the worker does for a request, and it runs from the
    worker's own error handler — so an exception escaping here would end the
    only queue consumer and leave every later request queued forever, with no
    error visible anywhere. Catches every exception rather than
    TelegramAPIError, because a Telegram front end answering with an HTML error
    page raises ClientDecodeError, which is not one of those.
    """
    try:
        await request.reporter.finish(text)
    except Exception as exc:
        logger.warning(
            "could not report the outcome to %s: %s: %s",
            request.chat_id,
            type(exc).__name__,
            exc,
        )
