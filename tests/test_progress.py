"""One status message per request, and the pace at which it is allowed to change."""

import asyncio

from aiogram.methods import DeleteMessage, EditMessageText

from tests.helpers.bot_harness import BotHarness
from twitter_dl.bot.progress import ProgressReporter


def build_reporter(harness: BotHarness, *, min_interval: float = 0.05) -> ProgressReporter:
    return ProgressReporter(harness.bot, chat_id=1, message_id=777, min_interval=min_interval)


def edits(harness: BotHarness) -> list[str]:
    return [
        text
        for method in harness.session.calls_of(EditMessageText)
        if (text := getattr(method, "text", None))
    ]


async def test_a_state_change_is_shown_at_once(harness: BotHarness) -> None:
    reporter = build_reporter(harness)

    await reporter.set("Downloading…")

    assert edits(harness) == ["Downloading…"]


async def test_repeating_what_is_already_on_screen_costs_nothing(harness: BotHarness) -> None:
    reporter = build_reporter(harness)

    await reporter.set("Downloading…")
    await reporter.set("Downloading…")

    assert len(edits(harness)) == 1


async def test_a_burst_of_progress_becomes_one_edit_not_a_hundred(harness: BotHarness) -> None:
    reporter = build_reporter(harness, min_interval=10.0)

    for percent in range(100):
        reporter.offer(f"{percent}%")
    await asyncio.sleep(0.05)

    # yt-dlp reports many times a second; Telegram would start rejecting edits
    # long before that, so only the first of the burst may land.
    assert len(edits(harness)) <= 1
    await reporter.close()


async def test_progress_resumes_once_the_throttle_window_passes(harness: BotHarness) -> None:
    reporter = build_reporter(harness, min_interval=0.05)

    reporter.offer("10%")
    await asyncio.sleep(0.02)
    reporter.offer("90%")
    await asyncio.sleep(0.2)

    assert "90%" in edits(harness)
    await reporter.close()


async def test_a_state_change_outranks_progress_that_was_waiting_its_turn(
    harness: BotHarness,
) -> None:
    reporter = build_reporter(harness, min_interval=0.2)

    reporter.offer("10%")
    await asyncio.sleep(0.01)
    reporter.offer("20%")
    await reporter.set("Uploading to Telegram…")
    await asyncio.sleep(0.3)

    # The stale percentage must not overwrite the newer state.
    assert edits(harness)[-1] == "Uploading to Telegram…"
    await reporter.close()


async def test_the_last_word_stops_further_updates(harness: BotHarness) -> None:
    reporter = build_reporter(harness, min_interval=0.05)

    reporter.offer("50%")
    await reporter.finish("Gave up after 30 minutes.")
    await asyncio.sleep(0.15)

    assert edits(harness)[-1] == "Gave up after 30 minutes."


async def test_a_delivered_video_replaces_the_status_message(harness: BotHarness) -> None:
    reporter = build_reporter(harness)
    await reporter.set("Uploading to Telegram…")

    await reporter.replace_with_upload()

    assert harness.session.calls_of(DeleteMessage)


async def test_a_status_message_that_cannot_be_edited_never_fails_the_request(
    harness: BotHarness,
) -> None:
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText

    harness.session.fail_on["EditMessageText"] = TelegramBadRequest(
        method=EditMessageText(chat_id=1, message_id=777, text="x"),
        message="message to edit not found",
    )
    reporter = build_reporter(harness)

    await reporter.set("Downloading…")  # must not raise
