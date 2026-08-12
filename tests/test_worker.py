"""The queue's bound, and what the worker does with each outcome."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage

from tests.helpers.bot_harness import BotHarness
from tests.helpers.factories import OWNER_ID, build_settings, make_clip
from twitter_dl.bot import texts
from twitter_dl.bot.progress import ProgressReporter
from twitter_dl.config import Settings
from twitter_dl.domain import Clip, ProgressCallback
from twitter_dl.errors import AuthExpired, NoVideoInTweet, TweetUnavailable
from twitter_dl.runtime.worker import (
    OwnerAlerts,
    Request,
    RequestQueue,
    RequestWorker,
)
from twitter_dl.services.delivery import ChatDelivery, DeliveryResult, ShareDelivery

TWEET = "https://x.com/someone/status/1234567890"


class FakeDownloader:
    def __init__(
        self,
        *,
        clips: Callable[[Path], list[Clip]] | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._clips = clips or (lambda dest: [make_clip(dest)])
        self._error = error
        self._delay = delay
        self.destinations: list[Path] = []

    async def download(
        self, url: str, dest: Path, *, on_progress: ProgressCallback | None = None
    ) -> list[Clip]:
        self.destinations.append(dest)
        if on_progress is not None:
            on_progress("50%")
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._clips(dest)


class FakeDelivery:
    def __init__(self, *, result: DeliveryResult | None = None) -> None:
        self._result = result
        self.delivered: list[tuple[Clip, str, int, int]] = []

    async def deliver(
        self, clip: Clip, *, chat_id: int, caption: str, index: int = 1, total: int = 1
    ) -> DeliveryResult:
        self.delivered.append((clip, caption, index, total))
        return self._result or ChatDelivery(size_bytes=clip.path.stat().st_size)


def build_worker(
    harness: BotHarness,
    settings: Settings,
    *,
    downloader: FakeDownloader | None = None,
    delivery: FakeDelivery | None = None,
    alerts: OwnerAlerts | None = None,
) -> RequestWorker:
    return RequestWorker(
        queue=harness.queue,
        downloader=downloader or FakeDownloader(),
        delivery=delivery or FakeDelivery(),
        alerts=alerts or OwnerAlerts(harness.bot, owner_id=settings.owner_id, cookies_file=None),
        settings=settings,
    )


def make_request(harness: BotHarness, url: str = TWEET) -> Request:
    reporter = ProgressReporter(harness.bot, chat_id=OWNER_ID, message_id=777, min_interval=0.0)
    return Request(url=url, chat_id=OWNER_ID, user_id=OWNER_ID, reporter=reporter)


@asynccontextmanager
async def running(worker: RequestWorker) -> AsyncIterator[None]:
    task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def drain(queue: RequestQueue, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while queue.load:
            await asyncio.sleep(0.01)


def edited_texts(harness: BotHarness) -> list[str]:
    return [
        text
        for method in harness.session.calls_of(EditMessageText)
        if (text := getattr(method, "text", None))
    ]


async def test_the_queue_counts_the_request_being_worked_on_not_just_those_waiting(
    harness: BotHarness,
) -> None:
    queue = RequestQueue(limit=2)
    first, second = make_request(harness), make_request(harness)

    assert queue.submit(first) == 1
    assert queue.submit(second) == 2
    with pytest.raises(asyncio.QueueFull):
        queue.submit(make_request(harness))

    await queue.take()  # in flight, so the slot is still occupied
    assert queue.load == 2
    with pytest.raises(asyncio.QueueFull):
        queue.submit(make_request(harness))

    queue.release()
    assert queue.load == 1


async def test_a_clip_is_delivered_and_the_status_message_steps_aside(
    harness: BotHarness, settings: Settings
) -> None:
    delivery = FakeDelivery()
    worker = build_worker(harness, settings, delivery=delivery)

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)

    assert [caption for _, caption, _, _ in delivery.delivered] == [TWEET]
    # The video itself is the answer, so the progress message is removed.
    assert harness.session.calls_of(DeleteMessage)


async def test_every_clip_of_a_tweet_is_delivered_and_numbered(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader(
        clips=lambda dest: [
            make_clip(dest, name="one.mp4"),
            make_clip(dest, name="two.mp4"),
        ]
    )
    delivery = FakeDelivery()
    worker = build_worker(harness, settings, downloader=downloader, delivery=delivery)

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)

    assert [(index, total) for _, _, index, total in delivery.delivered] == [(1, 2), (2, 2)]


async def test_an_oversized_clip_is_reported_by_its_path_on_the_share(
    harness: BotHarness, settings: Settings
) -> None:
    share = ShareDelivery(size_bytes=120 * 1024 * 1024, display_path=r"\\router\share\clip.mp4")
    worker = build_worker(harness, settings, delivery=FakeDelivery(result=share))

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)

    assert any(share.display_path in text for text in edited_texts(harness))
    # The path IS the result, so nothing is deleted.
    assert not harness.session.calls_of(DeleteMessage)


async def test_the_scratch_directory_never_outlives_the_request(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader()
    worker = build_worker(harness, settings, downloader=downloader)

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)

    assert downloader.destinations and not downloader.destinations[0].exists()


async def test_scratch_is_cleaned_up_after_a_failure_too(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader(error=TweetUnavailable("gone"))
    worker = build_worker(harness, settings, downloader=downloader)

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)

    assert downloader.destinations and not downloader.destinations[0].exists()
    assert texts.TWEET_UNAVAILABLE in edited_texts(harness)


async def test_each_failure_class_gets_its_own_explanation(
    harness: BotHarness, settings: Settings
) -> None:
    worker = build_worker(harness, settings, downloader=FakeDownloader(error=NoVideoInTweet("x")))

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)

    assert texts.NO_VIDEO in edited_texts(harness)


async def test_a_stalled_download_is_abandoned_and_the_user_told(
    harness: BotHarness, tmp_path: Path
) -> None:
    settings = build_settings(tmp_path, download_timeout_s=1)
    worker = build_worker(harness, settings, downloader=FakeDownloader(delay=30))

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue, timeout=5.0)

    assert texts.TIMED_OUT.format(minutes=0) in edited_texts(harness)


async def test_an_unexpected_crash_does_not_wedge_the_queue_for_everyone_else(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader(error=RuntimeError("something nobody predicted"))
    worker = build_worker(harness, settings, downloader=downloader)

    async with running(worker):
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)
        harness.queue.submit(make_request(harness))
        await drain(harness.queue)

    assert len(downloader.destinations) == 2
    assert texts.DOWNLOAD_FAILED in edited_texts(harness)


class TestOwnerAlerts:
    """The one failure worth waking the owner for, and how often."""

    async def test_expired_cookies_reach_the_owner_and_only_the_owner(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("stale")
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id, cookies_file=cookies)
        worker = build_worker(
            harness, settings, downloader=FakeDownloader(error=AuthExpired("NSFW")), alerts=alerts
        )

        async with running(worker):
            harness.queue.submit(make_request(harness))
            await drain(harness.queue)

        alerts_sent = harness.session.calls_of(SendMessage)
        assert len(alerts_sent) == 1
        assert alerts_sent[0].chat_id == settings.owner_id
        assert str(cookies) in alerts_sent[0].text
        # The person who asked is told something useful, but not the details.
        assert texts.AUTH_EXPIRED in edited_texts(harness)

    async def test_the_owner_is_not_told_twice_about_the_same_dead_session(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("stale")
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id, cookies_file=cookies)

        await alerts.auth_expired("NSFW")
        await alerts.auth_expired("NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 1

    async def test_replacing_the_cookie_file_re_arms_the_alert(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("stale")
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id, cookies_file=cookies)
        await alerts.auth_expired("NSFW")

        # A fresh export: same path, new mtime.
        stat = cookies.stat()
        cookies.write_text("fresh")
        import os

        os.utime(cookies, (stat.st_atime + 10, stat.st_mtime + 10))
        await alerts.auth_expired("NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 2
