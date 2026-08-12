"""Process entrypoint: long polling. Run as ``python -m twitter_dl``."""

import asyncio
import contextlib
import fcntl
import logging
import os
import signal
import socket
import sys
import time
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BotCommand, ErrorEvent

from twitter_dl.bot import texts
from twitter_dl.bot.handlers import fallback, links, start
from twitter_dl.bot.middlewares import AuthMiddleware, PrivateChatOnlyMiddleware
from twitter_dl.bot.storage import NoStorage
from twitter_dl.config import Settings
from twitter_dl.runtime.watchdog import run_watchdog, sd_notify
from twitter_dl.runtime.worker import OwnerAlerts, RequestQueue, RequestWorker
from twitter_dl.services.cookies import CookieSession
from twitter_dl.services.delivery import ClipDelivery
from twitter_dl.services.downloader import YtDlpDownloader, ffmpeg_available

logger = logging.getLogger("twitter_dl")

_LOCK_NAME = "twitter-dl.lock"
_ALREADY_RUNNING = "Already running — a second copy would fight over getUpdates."

# aiogram waits (session timeout + polling timeout) on every getUpdates, so the
# defaults hide a dead socket for a minute and a half. Tightened to ~35s: still
# an honest long poll, but a broken tunnel is noticed and reconnected quickly.
# This is the per-request default for *all* Bot API calls, which is why the
# video upload passes its own much larger timeout (see services/delivery.py).
_SESSION_TIMEOUT_S = 15
_POLLING_TIMEOUT_S = 20
# Liveness cadence. The supervisor's WatchdogSec must be comfortably larger, or
# one slow probe reads as a hang; the server pairs these 30s with 90s.
_WATCHDOG_INTERVAL_S = 30
_WATCHDOG_PROBE_TIMEOUT_S = 10
# Startup retry budget for reaching Telegram. The proxy may still be coming up
# when the bot starts, which is a blip, not a failure — but an unbounded retry
# would hide a genuine misconfiguration (a typo in the proxy URL) forever, so
# the wait ends and the supervisor gets to see a failed start.
_CONNECT_RETRY_START_S = 3.0
_CONNECT_RETRY_MAX_S = 30.0
_CONNECT_BUDGET_S = 600.0
_START_EXTEND_USEC = 120 * 1_000_000
# 5xx and 429 are TelegramAPIError siblings rather than network errors, and both
# pass on their own — treating them as fatal would burn the restart limit.
_RETRYABLE = (TelegramNetworkError, TelegramServerError, TelegramRetryAfter)


def build_dispatcher(settings: Settings, queue: RequestQueue) -> Dispatcher:
    """Wire the dispatcher in the one order that matters.

    Shared with the test harness so the two can never drift: the gate order
    below *is* the access policy, and a test that assembled its own dispatcher
    would be testing a different bot.
    """
    # NoStorage, not the default MemoryStorage: aiogram resolves an FSM context
    # before any middleware registered here can run, so with a storage that
    # remembers keys, every stranger who messages the bot leaves a record behind
    # despite being refused. There are no dialogs to store anyway.
    dp = Dispatcher(storage=NoStorage())
    # Workflow data: aiogram hands these to any handler declaring a parameter of
    # the same name. With no database there is nothing request-scoped to build,
    # so a DI container would be ceremony around two singletons.
    dp["settings"] = settings
    dp["queue"] = queue

    # Both gates are outer middlewares on `update`, before any filter runs: a
    # stranger's message must not even be pattern-matched, let alone answered.
    dp.update.outer_middleware(PrivateChatOnlyMiddleware())
    dp.update.outer_middleware(AuthMiddleware(settings.allowed_ids))

    dp.include_router(start.router)
    dp.include_router(links.router)
    dp.include_router(fallback.router)  # must be last: it matches any message

    dp.errors.register(on_error)
    return dp


async def on_error(event: ErrorEvent) -> bool:
    """Nothing may fail silently in a chat: the user gets a verdict either way."""
    logger.exception("handler failed: %s", event.exception)
    message = event.update.message
    if message is not None:
        with contextlib.suppress(TelegramAPIError):
            await message.answer(texts.DOWNLOAD_FAILED)
    return True


def _acquire_single_instance_lock() -> Any:
    r"""Guard against a second copy: two would fight over getUpdates (Telegram 409).

    Both variants hand the guarantee to the kernel, which releases the lock when
    the process dies, so it cannot go stale:

    * Linux — an abstract unix socket (leading NUL), leaving no file behind.
    * Elsewhere (the FreeBSD server) — flock on a real file, because the
      abstract namespace does not exist there and bind("\0…") fails with ENOENT.
    """
    if sys.platform.startswith("linux"):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind("\0" + _LOCK_NAME)
        except OSError:
            raise SystemExit(_ALREADY_RUNNING) from None
        return sock

    lock_path = os.environ.get("LOCK_FILE") or os.path.join("/tmp", _LOCK_NAME)
    handle = open(lock_path, "w")  # noqa: SIM115 — held open for the process's life
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(_ALREADY_RUNNING) from None
    return handle


async def _establish_connection(bot: Bot) -> Any:
    """Wait for Telegram to be reachable through the configured proxy.

    A proxy that is down at startup is transient; exiting would burn the
    supervisor's restart limit over a blip. Fatal API errors (a bad token → 401)
    are re-raised untouched, since no amount of retrying fixes those.
    """
    delay = _CONNECT_RETRY_START_S
    attempt = 0
    deadline = time.monotonic() + _CONNECT_BUDGET_S
    while True:
        attempt += 1
        try:
            # me() rather than get_me(): aiogram caches the result.
            me = await bot.me()
            await bot.delete_webhook(drop_pending_updates=False)
            return me
        except Exception as exc:
            if isinstance(exc, TelegramAPIError) and not isinstance(exc, _RETRYABLE):
                raise
            if time.monotonic() >= deadline:
                logger.error(
                    "Telegram unreachable for %.0fs (%d attempts) — giving up so the "
                    "supervisor sees a failed start",
                    _CONNECT_BUDGET_S,
                    attempt,
                )
                raise
            sd_notify(f"EXTEND_TIMEOUT_USEC={_START_EXTEND_USEC}")
            logger.warning(
                "Telegram unreachable at startup (attempt %d): %r. Retrying in %gs.",
                attempt,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _CONNECT_RETRY_MAX_S)


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands([BotCommand(command="help", description="What this bot does")])


async def _run_bot(settings: Settings) -> None:
    session = (
        AiohttpSession(proxy=settings.telegram_proxy, timeout=_SESSION_TIMEOUT_S)
        if settings.telegram_proxy
        else AiohttpSession(timeout=_SESSION_TIMEOUT_S)
    )
    bot = Bot(token=settings.bot_token, session=session)

    queue = RequestQueue(settings.queue_limit)
    cookies = CookieSession(settings.cookies_file)
    worker = RequestWorker(
        queue=queue,
        downloader=YtDlpDownloader(cookies=cookies, proxy=settings.ytdlp_proxy),
        delivery=ClipDelivery(
            bot,
            max_chat_bytes=settings.max_tg_video_bytes,
            rclone_binary=settings.rclone_binary,
            rclone_config=settings.rclone_config,
            rclone_remote=settings.rclone_remote,
            share_path_prefix=settings.share_path_prefix,
        ),
        alerts=OwnerAlerts(bot, owner_id=settings.owner_id, cookies=cookies),
        settings=settings,
    )
    dp = build_dispatcher(settings, queue)

    me = await _establish_connection(bot)
    await _set_commands(bot)
    logger.info(
        "bot @%s started (long polling), %d user(s) allowed",
        me.username,
        len(settings.allowed_ids),
    )

    # Telegram answered, so readiness is now an honest claim.
    sd_notify("READY=1")
    worker_task = asyncio.create_task(worker.run())
    worker_task.add_done_callback(_worker_died)
    watchdog_task = asyncio.create_task(
        run_watchdog(bot, interval=_WATCHDOG_INTERVAL_S, probe_timeout=_WATCHDOG_PROBE_TIMEOUT_S)
    )
    try:
        # Only messages are used; asking for anything else would have Telegram
        # queue updates nobody reads.
        #
        # close_bot_session=False: aiogram's own finally closes the session
        # before start_polling returns, which would tear the connector out from
        # under an upload still finishing in the worker. We close it below,
        # after the worker has actually stopped.
        await dp.start_polling(
            bot,
            allowed_updates=["message"],
            polling_timeout=_POLLING_TIMEOUT_S,
            close_bot_session=False,
        )
    finally:
        worker_task.remove_done_callback(_worker_died)
        worker_task.cancel()
        watchdog_task.cancel()
        # Awaited rather than fired and forgotten: cancel() only schedules the
        # CancelledError, and closing the session underneath a running upload
        # turns a clean shutdown into a traceback.
        await asyncio.gather(worker_task, watchdog_task, return_exceptions=True)
        await bot.session.close()


def _worker_died(task: asyncio.Task[None]) -> None:
    """Turn a dead queue consumer into a dead process.

    The worker is the only thing that downloads anything. If it ever stops on
    its own, the bot keeps answering "Queued…" forever while nothing happens —
    and the watchdog keeps reporting health, because Telegram is still
    reachable. A crash the supervisor can see and restart is far better than a
    bot that looks alive and does nothing.
    """
    if task.cancelled():
        return
    exception = task.exception()
    if exception is None:
        logger.error("request worker stopped on its own — exiting so the supervisor restarts us")
    else:
        logger.critical(
            "request worker died: %r — exiting so the supervisor restarts us", exception
        )
    # SIGTERM rather than sys.exit(): this runs inside a callback on the loop,
    # where raising would only be logged as "exception in callback".
    os.kill(os.getpid(), signal.SIGTERM)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    )
    # Held by main()'s frame for the process's lifetime: the kernel drops the
    # lock when the process dies, so it is enough to keep it from being
    # garbage-collected early.
    _lock = _acquire_single_instance_lock()  # noqa: F841

    settings = Settings()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        logger.warning(texts.FFMPEG_MISSING)
    asyncio.run(_run_bot(settings))


if __name__ == "__main__":
    # SystemExit from the single-instance guard is deliberately not suppressed.
    with contextlib.suppress(KeyboardInterrupt):
        main()
