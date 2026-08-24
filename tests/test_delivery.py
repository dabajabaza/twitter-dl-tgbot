"""Size chooses Chat or the Request's captured Overflow destination."""

from datetime import date
from pathlib import Path

import pytest
from aiogram.enums import ParseMode
from aiogram.methods import SendVideo

from clipivore.errors import OverflowFailed, OverflowUnavailable
from clipivore.services.delivery import (
    ChatDelivery,
    ClipDelivery,
    OverflowDelivery,
    overflow_name,
)
from clipivore.services.overflow import OverflowChoice, OverflowDestination, OverflowState
from tests.helpers.bot_harness import BotHarness
from tests.helpers.factories import make_clip


class FakeDestination(OverflowDestination):
    label = "Somewhere"

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.stored: list[tuple[Path, str]] = []

    async def store(self, source: Path, *, name: str) -> str:
        self.stored.append((source, name))
        if self.error is not None:
            raise self.error
        return f"https://files.example/{name}"


def ready(destination: FakeDestination | None = None) -> OverflowChoice:
    target = destination or FakeDestination()
    return OverflowChoice(
        adapter_id="somewhere",
        label=target.label,
        state=OverflowState.READY,
        destination=target,
    )


OFF = OverflowChoice(adapter_id="none", label="Off", state=OverflowState.OFF)


def build_delivery(harness: BotHarness, *, max_chat_mb: int = 50) -> ClipDelivery:
    return ClipDelivery(harness.bot, max_chat_bytes=max_chat_mb * 1024 * 1024)


@pytest.mark.parametrize(
    "overflow",
    [
        OFF,
        OverflowChoice("gone", "Gone", OverflowState.MISSING),
        OverflowChoice("broken", "Broken", OverflowState.MISCONFIGURED),
    ],
)
async def test_a_clip_within_the_limit_ignores_overflow_and_goes_to_the_chat(
    harness: BotHarness,
    tmp_path: Path,
    overflow: OverflowChoice,
) -> None:
    clip = make_clip(tmp_path, size_bytes=1024)

    result = await build_delivery(harness).deliver(
        clip,
        chat_id=42,
        caption="https://x.com/a/status/1",
        overflow=overflow,
    )

    assert isinstance(result, ChatDelivery)
    sent = harness.session.calls_of(SendVideo)
    assert len(sent) == 1
    assert sent[0].caption == "https://x.com/a/status/1"
    assert sent[0].parse_mode == ParseMode.HTML
    assert sent[0].chat_id == 42


async def test_a_clip_over_the_limit_uses_the_captured_adapter(
    harness: BotHarness, tmp_path: Path
) -> None:
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)
    destination = FakeDestination()

    result = await build_delivery(harness, max_chat_mb=1).deliver(
        clip,
        chat_id=42,
        caption="ignored",
        overflow=ready(destination),
    )

    assert isinstance(result, OverflowDelivery)
    assert result.adapter_label == destination.label
    assert result.location.startswith("https://files.example/")
    assert destination.stored == [(clip.path, overflow_name(clip))]
    assert not harness.session.calls_of(SendVideo)


async def test_a_final_file_over_the_limit_names_an_unavailable_adapter(
    harness: BotHarness, tmp_path: Path
) -> None:
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)

    with pytest.raises(OverflowUnavailable, match="'none' is off"):
        await build_delivery(harness, max_chat_mb=1).deliver(
            clip,
            chat_id=42,
            caption="ignored",
            overflow=OFF,
        )


async def test_an_adapter_failure_is_not_reported_as_a_download_failure(
    harness: BotHarness, tmp_path: Path
) -> None:
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)
    destination = FakeDestination(error=RuntimeError("quota exceeded"))

    with pytest.raises(OverflowFailed, match="quota exceeded"):
        await build_delivery(harness, max_chat_mb=1).deliver(
            clip,
            chat_id=42,
            caption="ignored",
            overflow=ready(destination),
        )


async def test_an_empty_adapter_locator_is_an_overflow_failure(
    harness: BotHarness, tmp_path: Path
) -> None:
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)
    destination = FakeDestination()

    async def empty_store(source: Path, *, name: str) -> str:
        return "  "

    destination.store = empty_store  # type: ignore[method-assign]

    with pytest.raises(OverflowFailed, match="empty locator"):
        await build_delivery(harness, max_chat_mb=1).deliver(
            clip,
            chat_id=42,
            caption="ignored",
            overflow=ready(destination),
        )


async def test_system_exit_from_an_adapter_is_an_overflow_failure(
    harness: BotHarness, tmp_path: Path
) -> None:
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)

    with pytest.raises(OverflowFailed):
        await build_delivery(harness, max_chat_mb=1).deliver(
            clip,
            chat_id=42,
            caption="ignored",
            overflow=ready(FakeDestination(error=SystemExit("bad plugin"))),
        )


async def test_an_adapter_locator_is_normalized_only_once(
    harness: BotHarness, tmp_path: Path
) -> None:
    class StatefulLocator(str):
        calls = 0

        def strip(self, chars: str | None = None) -> str:
            type(self).calls += 1
            return "   "

    destination = FakeDestination()

    async def stateful_store(source: Path, *, name: str) -> str:
        return StatefulLocator(" https://files.example/clip ")

    destination.store = stateful_store  # type: ignore[method-assign]
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)

    result = await build_delivery(harness, max_chat_mb=1).deliver(
        clip,
        chat_id=42,
        caption="ignored",
        overflow=ready(destination),
    )

    assert isinstance(result, OverflowDelivery)
    assert result.location == "https://files.example/clip"
    assert StatefulLocator.calls == 0


class TestOverflowName:
    def test_it_sorts_by_date_and_greps_by_author_and_post(self, tmp_path: Path) -> None:
        clip = make_clip(
            tmp_path,
            post_id="1234567890",
            uploader="someone",
            upload_date=date(2026, 8, 13),
        )
        assert overflow_name(clip) == "2026-08-13-someone-1234567890.mp4"

    def test_several_clips_from_one_tweet_do_not_overwrite_each_other(self, tmp_path: Path) -> None:
        clip = make_clip(tmp_path)
        assert overflow_name(clip, index=2, total=2).endswith("-2.mp4")

    def test_a_hostile_handle_cannot_escape_the_file_name(self, tmp_path: Path) -> None:
        clip = make_clip(tmp_path, uploader="../../etc/passwd")
        name = overflow_name(clip)
        assert "/" not in name and ".." not in name


# The caption-length guarantee lives upstream now: bot/captions.build_caption
# fits the text before the caption ever reaches deliver() (tests/test_captions.py).
class TestUploadDetails:
    async def test_uploads_get_their_own_generous_timeout(
        self, harness: BotHarness, tmp_path: Path
    ) -> None:
        clip = make_clip(tmp_path, size_bytes=1024)

        await build_delivery(harness).deliver(
            clip,
            chat_id=42,
            caption="x",
            overflow=OFF,
        )

        assert harness.session.timeout_of(SendVideo) == 600
