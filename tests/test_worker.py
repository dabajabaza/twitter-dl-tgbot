"""The queue's bound, and what the worker does with each outcome."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiogram.exceptions import ClientDecodeError, TelegramNetworkError
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage

from clipivore.bot import texts
from clipivore.bot.captions import build_caption
from clipivore.bot.progress import ProgressReporter
from clipivore.config import Settings
from clipivore.domain import Clip, DownloadProgress, ProgressCallback
from clipivore.errors import AuthExpired, DownloadTooLarge, NoVideoInPost, PostUnavailable
from clipivore.runtime.worker import (
    OwnerAlerts,
    Request,
    RequestQueue,
    RequestWorker,
    SourceMessage,
    _progress_into,
)
from clipivore.services.cookies import CookieSession
from clipivore.services.delivery import ChatDelivery, DeliveryResult, OverflowDelivery
from clipivore.services.overflow import OverflowChoice, OverflowDestination, OverflowState
from clipivore.services.providers import ProviderChoice
from tests.helpers.bot_harness import BotHarness
from tests.helpers.factories import (
    OWNER_ID,
    build_settings,
    make_clip,
    make_provider_choice,
)

# The name make_provider_choice gives its Provider; every verdict quotes it.
PROVIDER_NAME = "X"

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
        self.max_bytes: list[int | None] = []

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        on_progress: ProgressCallback | None = None,
        max_bytes: int | None = None,
    ) -> list[Clip]:
        self.destinations.append(dest)
        self.max_bytes.append(max_bytes)
        if on_progress is not None:
            on_progress(DownloadProgress(text="50% of 82 MB", stream="video"))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._clips(dest)


class FakeDelivery:
    def __init__(self, *, result: DeliveryResult | None = None) -> None:
        self._result = result
        self.delivered: list[tuple[Clip, str, OverflowChoice, int, int]] = []

    async def deliver(
        self,
        clip: Clip,
        *,
        chat_id: int,
        caption: str,
        overflow: OverflowChoice,
        index: int = 1,
        total: int = 1,
    ) -> DeliveryResult:
        self.delivered.append((clip, caption, overflow, index, total))
        return self._result or ChatDelivery(size_bytes=clip.path.stat().st_size)


def build_worker(
    harness: BotHarness,
    settings: Settings,
    *,
    delivery: FakeDelivery | None = None,
    alerts: OwnerAlerts | None = None,
) -> RequestWorker:
    """The worker owns no engine any more — every Request brings its Provider."""
    return RequestWorker(
        queue=harness.queue,
        delivery=delivery or FakeDelivery(),
        alerts=alerts or OwnerAlerts(harness.bot, owner_id=settings.owner_id),
        settings=settings,
    )


class ReadyDestination(OverflowDestination):
    label = "Test"

    async def store(self, source: Path, *, name: str) -> str:
        return name


OFF = OverflowChoice(adapter_id="none", label="Off", state=OverflowState.OFF)
READY = OverflowChoice(
    adapter_id="test",
    label="Test",
    state=OverflowState.READY,
    destination=ReadyDestination(),
)


# The user's message and the status message must be told apart in DeleteMessage
# asserts: the status one is 777 (or 5001+ for harness-created), the user's 555.
USER_MESSAGE_ID = 555


def make_request(
    harness: BotHarness,
    url: str = TWEET,
    *,
    overflow: OverflowChoice = OFF,
    source: SourceMessage | None = None,
    downloader: FakeDownloader | None = None,
    provider: ProviderChoice | None = None,
) -> Request:
    reporter = ProgressReporter(harness.bot, chat_id=OWNER_ID, message_id=777, min_interval=0.0)
    return Request(
        url=url,
        chat_id=OWNER_ID,
        user_id=OWNER_ID,
        reporter=reporter,
        overflow=overflow,
        provider=provider or make_provider_choice(downloader=downloader or FakeDownloader()),
        source=source,
    )


def make_source(harness: BotHarness, *, expected: int = 1) -> SourceMessage:
    return SourceMessage(
        harness.bot, chat_id=OWNER_ID, message_id=USER_MESSAGE_ID, expected=expected
    )


def deleted_ids(harness: BotHarness) -> list[int]:
    return [call.message_id for call in harness.session.calls_of(DeleteMessage)]


@asynccontextmanager
async def running(worker: RequestWorker) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(worker.run())
    try:
        yield task
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

    # make_clip carries no tweet text, so the caption is just the footer.
    assert [caption for _, caption, _, _, _ in delivery.delivered] == [
        build_caption("", TWEET, provider=PROVIDER_NAME, uploader="someone")
    ]
    # The upload status answers "why is this taking long": it names the size.
    assert texts.UPLOADING.format(size="1 KB") in edited_texts(harness)
    # The video itself is the answer, so the progress message is removed.
    assert harness.session.calls_of(DeleteMessage)


async def test_download_progress_names_the_stream_it_reports(harness: BotHarness) -> None:
    reporter = ProgressReporter(harness.bot, chat_id=OWNER_ID, message_id=777, min_interval=0.0)
    report = _progress_into(reporter)

    report(DownloadProgress(text="50% of 82 MB", stream="video"))
    await asyncio.sleep(0.05)
    report(DownloadProgress(text="12% of 3 MB", stream="audio"))
    await asyncio.sleep(0.05)

    statuses = edited_texts(harness)
    assert texts.DOWNLOADING_VIDEO_PROGRESS.format(progress="50% of 82 MB") in statuses
    assert texts.DOWNLOADING_AUDIO_PROGRESS.format(progress="12% of 3 MB") in statuses
    await reporter.close()


async def test_a_request_without_working_overflow_caps_the_download_early(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader()
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(make_request(harness, overflow=OFF, downloader=downloader))
        await drain(harness.queue)

    assert downloader.max_bytes == [settings.max_tg_video_bytes]


async def test_a_request_with_working_overflow_keeps_best_quality_unbounded(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader()
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(make_request(harness, overflow=READY, downloader=downloader))
        await drain(harness.queue)

    assert downloader.max_bytes == [None]


async def test_crossing_the_limit_with_overflow_off_gets_an_explicit_verdict(
    harness: BotHarness, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    error = DownloadTooLarge(
        limit_bytes=settings.max_tg_video_bytes,
        observed_bytes=63 * 1024 * 1024,
    )
    worker = build_worker(harness, settings)

    with caplog.at_level("INFO"):
        async with running(worker):
            harness.queue.submit(
                make_request(harness, overflow=OFF, downloader=FakeDownloader(error=error))
            )
            await drain(harness.queue)

    expected = texts.OVERFLOW_DISABLED.format(
        max_mb=settings.max_tg_video_mb, observed=" (stopped at 63 MB)"
    )
    assert expected in edited_texts(harness)
    # The verdict text is shared with the final-size refusal; without this log
    # line an early abort would be indistinguishable from it on the server.
    assert "stopped early" in caplog.text


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
    worker = build_worker(harness, settings, delivery=delivery)

    async with running(worker):
        harness.queue.submit(make_request(harness, downloader=downloader))
        await drain(harness.queue)

    assert [(index, total) for _, _, _, index, total in delivery.delivered] == [
        (1, 2),
        (2, 2),
    ]
    assert texts.UPLOADING_MANY.format(index=1, total=2, size="1 KB") in edited_texts(harness)


async def test_final_size_refusal_delivers_none_of_a_multi_clip_request(
    harness: BotHarness, tmp_path: Path
) -> None:
    settings = build_settings(tmp_path, max_tg_video_mb=1)
    downloader = FakeDownloader(
        clips=lambda dest: [
            make_clip(dest, name="small.mp4", size_bytes=1),
            make_clip(dest, name="large.mp4", size_bytes=2 * 1024 * 1024),
        ]
    )
    delivery = FakeDelivery()
    worker = build_worker(harness, settings, delivery=delivery)

    async with running(worker):
        harness.queue.submit(make_request(harness, overflow=OFF, downloader=downloader))
        await drain(harness.queue)

    assert delivery.delivered == []
    expected = texts.OVERFLOW_DISABLED.format(max_mb=1, observed=" (the clip is 2 MB)")
    assert expected in edited_texts(harness)


async def test_an_oversized_clip_is_reported_by_its_overflow_locator(
    harness: BotHarness, settings: Settings
) -> None:
    overflow = OverflowDelivery(
        size_bytes=120 * 1024 * 1024,
        adapter_label="Share",
        location=r"\\router\share\clip.mp4",
    )
    worker = build_worker(harness, settings, delivery=FakeDelivery(result=overflow))

    async with running(worker):
        harness.queue.submit(make_request(harness, overflow=READY))
        await drain(harness.queue)

    verdict = next(text for text in edited_texts(harness) if overflow.location in text)
    # The source message is deleted on success, so the verdict itself must
    # carry the tweet's footer.
    assert texts.OPEN_IN.format(provider=PROVIDER_NAME) in verdict
    finish = next(
        call
        for call in harness.session.calls_of(EditMessageText)
        if call.text and overflow.location in call.text
    )
    assert finish.parse_mode == "HTML"
    # The path IS the result, so nothing is deleted.
    assert not harness.session.calls_of(DeleteMessage)


async def test_the_overflow_verdict_escapes_the_tweets_text(
    harness: BotHarness, settings: Settings
) -> None:
    overflow = OverflowDelivery(
        size_bytes=120 * 1024 * 1024,
        adapter_label="Share",
        location=r"\\router\share\clip.mp4",
    )
    downloader = FakeDownloader(clips=lambda dest: [make_clip(dest, description='a "<b>&" tweet')])
    worker = build_worker(harness, settings, delivery=FakeDelivery(result=overflow))

    async with running(worker):
        harness.queue.submit(make_request(harness, overflow=READY, downloader=downloader))
        await drain(harness.queue)

    verdict = next(text for text in edited_texts(harness) if overflow.location in text)
    assert "a &quot;&lt;b&gt;&amp;&quot; tweet" in verdict
    assert "<b>" not in verdict


class TestSourceMessageCleanup:
    """The user's own message goes away only once everything it asked for
    arrived; any failure leaves it in place so the link is not lost."""

    async def test_a_delivered_request_deletes_the_users_message(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        worker = build_worker(harness, settings)

        async with running(worker):
            harness.queue.submit(make_request(harness, source=make_source(harness)))
            await drain(harness.queue)

        assert USER_MESSAGE_ID in deleted_ids(harness)

    async def test_a_failed_request_leaves_the_users_message_alone(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        worker = build_worker(
            harness,
            settings,
        )

        async with running(worker):
            harness.queue.submit(
                make_request(
                    harness,
                    downloader=FakeDownloader(error=NoVideoInPost("x")),
                    source=make_source(harness),
                )
            )
            await drain(harness.queue)

        assert USER_MESSAGE_ID not in deleted_ids(harness)

    async def test_an_unexpected_crash_counts_as_a_failure(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        worker = build_worker(
            harness,
            settings,
        )

        async with running(worker):
            harness.queue.submit(
                make_request(
                    harness,
                    downloader=FakeDownloader(error=RuntimeError("nobody predicted")),
                    source=make_source(harness),
                )
            )
            await drain(harness.queue)

        assert USER_MESSAGE_ID not in deleted_ids(harness)

    async def test_a_message_with_two_links_is_deleted_once_after_the_second(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        worker = build_worker(harness, settings)
        source = make_source(harness, expected=2)

        async with running(worker):
            harness.queue.submit(make_request(harness, source=source))
            await drain(harness.queue)
            first_round = deleted_ids(harness).count(USER_MESSAGE_ID)
            harness.queue.submit(make_request(harness, source=source))
            await drain(harness.queue)

        assert first_round == 0
        assert deleted_ids(harness).count(USER_MESSAGE_ID) == 1

    async def test_one_failed_link_keeps_the_whole_message(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        source = make_source(harness, expected=2)
        failing = build_worker(
            harness,
            settings,
        )
        async with running(failing):
            harness.queue.submit(
                make_request(
                    harness, source=source, downloader=FakeDownloader(error=PostUnavailable("gone"))
                )
            )
            await drain(harness.queue)
        succeeding = build_worker(harness, settings)
        async with running(succeeding):
            harness.queue.submit(make_request(harness, source=source))
            await drain(harness.queue)

        assert USER_MESSAGE_ID not in deleted_ids(harness)

    async def test_an_overflow_delivery_also_counts_as_success(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        overflow = OverflowDelivery(
            size_bytes=120 * 1024 * 1024,
            adapter_label="Share",
            location=r"\\router\share\clip.mp4",
        )
        worker = build_worker(harness, settings, delivery=FakeDelivery(result=overflow))

        async with running(worker):
            harness.queue.submit(make_request(harness, overflow=READY, source=make_source(harness)))
            await drain(harness.queue)

        # Only the user's message goes: the status message IS the locator now.
        assert deleted_ids(harness) == [USER_MESSAGE_ID]

    async def test_a_deletion_that_fails_does_not_take_the_worker_with_it(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        harness.session.fail_on["DeleteMessage"] = ClientDecodeError(
            message="not JSON", original=ValueError("boom"), data="<html>502</html>"
        )
        downloader = FakeDownloader()
        worker = build_worker(harness, settings)

        async with running(worker) as task:
            harness.queue.submit(
                make_request(harness, downloader=downloader, source=make_source(harness))
            )
            await drain(harness.queue)
            assert not task.done()

        assert harness.queue.load == 0
        assert downloader.destinations


async def test_the_scratch_directory_never_outlives_the_request(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader()
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(make_request(harness, downloader=downloader))
        await drain(harness.queue)

    assert downloader.destinations and not downloader.destinations[0].exists()


async def test_scratch_is_cleaned_up_after_a_failure_too(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader(error=PostUnavailable("gone"))
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(make_request(harness, downloader=downloader))
        await drain(harness.queue)

    assert downloader.destinations and not downloader.destinations[0].exists()
    assert texts.POST_UNAVAILABLE.format(provider=PROVIDER_NAME) in edited_texts(harness)


async def test_each_failure_class_gets_its_own_explanation(
    harness: BotHarness, settings: Settings
) -> None:
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(
            make_request(harness, downloader=FakeDownloader(error=NoVideoInPost("x")))
        )
        await drain(harness.queue)

    assert texts.NO_VIDEO in edited_texts(harness)


async def test_a_stalled_download_is_abandoned_and_the_user_told(
    harness: BotHarness, tmp_path: Path
) -> None:
    settings = build_settings(tmp_path, download_timeout_s=1)
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(make_request(harness, downloader=FakeDownloader(delay=30)))
        await drain(harness.queue, timeout=5.0)

    assert texts.TIMED_OUT.format(minutes=0) in edited_texts(harness)


async def test_an_unexpected_crash_does_not_wedge_the_queue_for_everyone_else(
    harness: BotHarness, settings: Settings
) -> None:
    downloader = FakeDownloader(error=RuntimeError("something nobody predicted"))
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(make_request(harness, downloader=downloader))
        await drain(harness.queue)
        harness.queue.submit(make_request(harness, downloader=downloader))
        await drain(harness.queue)

    assert len(downloader.destinations) == 2
    assert texts.DOWNLOAD_FAILED in edited_texts(harness)


class TestOwnerAlerts:
    """The one failure worth waking the owner for, and how often."""

    def _session(self, tmp_path: Path, body: str = "stale") -> tuple[CookieSession, Path]:
        export = tmp_path / "cookies.txt"
        export.write_text(body)
        return CookieSession(export), export

    def _provider(self, cookies: CookieSession) -> ProviderChoice:
        """The alert now dedupes per Provider, so it needs one to talk about."""
        return make_provider_choice(downloader=FakeDownloader(), cookies=cookies)

    async def test_expired_cookies_reach_the_owner_and_only_the_owner(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        cookies, export = self._session(tmp_path)
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)
        worker = build_worker(harness, settings, alerts=alerts)

        async with running(worker):
            harness.queue.submit(
                make_request(
                    harness,
                    provider=make_provider_choice(
                        downloader=FakeDownloader(error=AuthExpired("NSFW")), cookies=cookies
                    ),
                )
            )
            await drain(harness.queue)

        sent = harness.session.calls_of(SendMessage)
        assert len(sent) == 1
        assert sent[0].chat_id == settings.owner_id
        # The owner is told which file to replace, not the bot's scratch copy.
        assert str(export) in sent[0].text
        # The person who asked is told something useful, but not the details.
        assert texts.AUTH_EXPIRED.format(provider=PROVIDER_NAME) in edited_texts(harness)

    async def test_two_providers_are_deduped_apart_from_each_other(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        # One dead session must not silence the alert for a different platform's
        # dead session — the dedup is per Provider, not per bot.
        first_export = tmp_path / "x-cookies.txt"
        first_export.write_text("stale")
        second_export = tmp_path / "other-cookies.txt"
        second_export.write_text("also stale")
        first = make_provider_choice(
            downloader=FakeDownloader(),
            provider_id="x",
            name="X",
            cookies=CookieSession(first_export),
        )
        second = make_provider_choice(
            downloader=FakeDownloader(),
            provider_id="other",
            name="Other",
            cookies=CookieSession(second_export),
        )
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)

        await alerts.auth_expired(first, "NSFW")
        await alerts.auth_expired(second, "NSFW")
        await alerts.auth_expired(first, "NSFW")
        await alerts.auth_expired(second, "NSFW")

        sent = harness.session.calls_of(SendMessage)
        assert len(sent) == 2
        assert [("X" in call.text, "Other" in call.text) for call in sent] == [
            (True, False),
            (False, True),
        ]

    async def test_a_provider_with_no_session_still_reaches_the_owner(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        # Bluesky holds no cookies. If that ever produced an AuthExpired, the
        # alert must still send — naming the env var rather than a file path.
        provider = make_provider_choice(
            downloader=FakeDownloader(), provider_id="bluesky", name="Bluesky", cookies=None
        )
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)

        await alerts.auth_expired(provider, "rejected")

        sent = harness.session.calls_of(SendMessage)
        assert len(sent) == 1
        assert "COOKIES_FILE" in sent[0].text

    async def test_the_owner_is_not_told_twice_about_the_same_dead_session(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        cookies, _ = self._session(tmp_path)
        provider = self._provider(cookies)
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)

        await alerts.auth_expired(provider, "NSFW")
        await alerts.auth_expired(provider, "NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 1

    async def test_downloading_does_not_count_as_the_owner_replacing_the_export(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        # yt-dlp rewrites the cookie file it is given after every run. If the
        # alert deduped on that file, every private tweet would look like a new
        # session and the owner would be spammed once per link.
        cookies, _ = self._session(tmp_path)
        provider = self._provider(cookies)
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)
        await alerts.auth_expired(provider, "NSFW")

        staged = cookies.stage_into(tmp_path / "req-1")
        assert staged is not None
        staged.write_text("rewritten by yt-dlp")
        await alerts.auth_expired(provider, "NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 1

    async def test_replacing_the_export_re_arms_the_alert(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        cookies, export = self._session(tmp_path)
        provider = self._provider(cookies)
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)
        await alerts.auth_expired(provider, "NSFW")

        export.write_text("a genuinely fresh export")
        await alerts.auth_expired(provider, "NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 2

    async def test_an_alert_that_never_sent_is_not_counted_as_delivered(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        # Losing this one signal to a flap of the proxy would leave the owner
        # permanently unaware that their session is dead.
        cookies, _ = self._session(tmp_path)
        provider = self._provider(cookies)
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)
        harness.session.fail_on["SendMessage"] = TelegramNetworkError(
            method=SendMessage(chat_id=1, text="x"), message="proxy is down"
        )

        await alerts.auth_expired(provider, "NSFW")
        harness.session.fail_on.clear()
        await alerts.auth_expired(provider, "NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 2

    async def test_a_file_being_swapped_right_now_is_not_mistaken_for_a_new_session(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        # An unreadable export means "cannot tell which session this is", not
        # "a fresh one". Treating it as fresh sends the owner a duplicate in
        # exactly the moment they are already replacing the file.
        cookies, export = self._session(tmp_path)
        provider = self._provider(cookies)
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)
        await alerts.auth_expired(provider, "NSFW")

        export.unlink()
        await alerts.auth_expired(provider, "NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 1

    async def test_the_alert_re_arms_once_the_new_export_lands(
        self, harness: BotHarness, settings: Settings, tmp_path: Path
    ) -> None:
        cookies, export = self._session(tmp_path)
        provider = self._provider(cookies)
        alerts = OwnerAlerts(harness.bot, owner_id=settings.owner_id)
        await alerts.auth_expired(provider, "NSFW")
        export.unlink()
        await alerts.auth_expired(provider, "NSFW")

        export.write_text("the replacement the owner just exported")
        await alerts.auth_expired(provider, "NSFW")

        assert len(harness.session.calls_of(SendMessage)) == 2


async def test_a_request_carrying_a_broken_provider_still_gets_a_named_verdict(
    harness: BotHarness, settings: Settings
) -> None:
    # Unreachable in a running bot — the handler refuses first — which is
    # exactly why it needs a test: nothing else would notice it rotting.
    worker = build_worker(harness, settings)

    async with running(worker):
        harness.queue.submit(
            make_request(
                harness,
                provider=make_provider_choice(
                    downloader=FakeDownloader(), name="Broken", ready=False
                ),
            )
        )
        await drain(harness.queue)

    assert texts.PROVIDER_MISCONFIGURED.format(provider="Broken") in edited_texts(harness)


class TestTheWorkerCannotDieQuietly:
    """It is the only consumer: if it stops, the bot accepts links forever and
    downloads nothing, while the watchdog still reports perfect health."""

    async def test_a_telegram_front_end_serving_html_does_not_end_the_worker(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        # ClientDecodeError is an AiogramError but NOT a TelegramAPIError, so
        # every `except TelegramAPIError` in the request path lets it through.
        harness.session.fail_on["EditMessageText"] = ClientDecodeError(
            message="not JSON", original=ValueError("boom"), data="<html>502 Bad Gateway</html>"
        )
        downloader = FakeDownloader()
        worker = build_worker(harness, settings)

        async with running(worker) as task:
            harness.queue.submit(make_request(harness, downloader=downloader))
            await drain(harness.queue)
            harness.session.fail_on.clear()
            harness.queue.submit(make_request(harness, downloader=downloader))
            await drain(harness.queue)

            assert not task.done()
        # Both requests were actually worked on, not just accepted.
        assert len(downloader.destinations) == 2

    async def test_a_reporter_that_cannot_speak_at_all_still_frees_the_slot(
        self, harness: BotHarness, settings: Settings
    ) -> None:
        harness.session.fail_on["EditMessageText"] = ClientDecodeError(
            message="not JSON", original=ValueError("boom"), data="<html>502</html>"
        )
        harness.session.fail_on["DeleteMessage"] = ClientDecodeError(
            message="not JSON", original=ValueError("boom"), data="<html>502</html>"
        )
        worker = build_worker(harness, settings)

        async with running(worker):
            harness.queue.submit(make_request(harness))
            await drain(harness.queue)

        assert harness.queue.load == 0


class TestTheDeadlineBoundsWorkNotTheVerdict:
    """The timeout exists to stop a download that will not end, not to
    interrupt the sentence that reports one that already did."""

    async def test_a_deadline_expiring_during_the_final_edit_still_leaves_a_verdict(
        self, harness: BotHarness, tmp_path: Path
    ) -> None:
        # The clip is delivered; only the closing status update is left. If
        # that update runs under the deadline, its cancellation freezes the
        # message on "Uploading…" forever — and on an Overflow route takes the
        # Adapter's only returned locator with it.
        settings = build_settings(tmp_path, download_timeout_s=1)
        worker = build_worker(harness, settings)

        reporter = SlowFinishReporter(harness.bot, chat_id=OWNER_ID, message_id=777)
        request = Request(
            url=TWEET,
            chat_id=OWNER_ID,
            user_id=OWNER_ID,
            reporter=reporter,
            overflow=OFF,
            provider=make_provider_choice(downloader=FakeDownloader()),
        )

        async with running(worker):
            harness.queue.submit(request)
            await drain(harness.queue, timeout=5.0)

        assert harness.session.calls_of(DeleteMessage)
        assert not any(text.startswith("Gave up") for text in edited_texts(harness))


class SlowFinishReporter(ProgressReporter):
    """A reporter whose closing update takes long enough to cross a deadline."""

    async def replace_with_upload(self) -> None:
        await asyncio.sleep(0.4)
        await super().replace_with_upload()
