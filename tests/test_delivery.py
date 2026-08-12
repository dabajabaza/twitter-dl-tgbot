"""Size decides the destination, and the share decides the name."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from aiogram.methods import SendVideo

from tests.helpers.bot_harness import BotHarness
from tests.helpers.factories import make_clip
from twitter_dl.errors import ShareUnavailable
from twitter_dl.services.delivery import (
    ChatDelivery,
    ClipDelivery,
    ShareDelivery,
    share_name,
)

SHARE_PREFIX = r"\\192.168.1.1\KeeneticShared\twitter-dl"


class FakeProcess:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture
def rclone_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Records rclone invocations instead of running them."""
    calls: list[list[str]] = []

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        calls.append(list(argv))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def build_delivery(harness: BotHarness, *, max_chat_mb: int = 50) -> ClipDelivery:
    return ClipDelivery(
        harness.bot,
        max_chat_bytes=max_chat_mb * 1024 * 1024,
        rclone_binary="rclone",
        rclone_config=Path("/usr/local/etc/backup/rclone.conf"),
        rclone_remote="keenetic:KeeneticShared/twitter-dl",
        share_path_prefix=SHARE_PREFIX,
    )


async def test_a_clip_within_the_limit_goes_to_the_chat_captioned_with_its_tweet(
    harness: BotHarness, tmp_path: Path
) -> None:
    clip = make_clip(tmp_path, size_bytes=1024)
    delivery = build_delivery(harness)

    result = await delivery.deliver(clip, chat_id=42, caption="https://x.com/a/status/1")

    assert isinstance(result, ChatDelivery)
    sent = harness.session.calls_of(SendVideo)
    assert len(sent) == 1
    assert sent[0].caption == "https://x.com/a/status/1"
    assert sent[0].chat_id == 42


async def test_a_clip_over_the_limit_goes_to_the_share_instead(
    harness: BotHarness, tmp_path: Path, rclone_calls: list[list[str]]
) -> None:
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)
    delivery = build_delivery(harness, max_chat_mb=1)

    result = await delivery.deliver(clip, chat_id=42, caption="ignored")

    assert isinstance(result, ShareDelivery)
    assert not harness.session.calls_of(SendVideo)
    assert result.display_path.startswith(SHARE_PREFIX)
    assert rclone_calls and rclone_calls[0][0] == "rclone"
    assert "copyto" in rclone_calls[0]
    assert rclone_calls[0][-1].endswith(share_name(clip))


async def test_the_configured_rclone_config_is_the_one_used(
    harness: BotHarness, tmp_path: Path, rclone_calls: list[list[str]]
) -> None:
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)

    await build_delivery(harness, max_chat_mb=1).deliver(clip, chat_id=42, caption="x")

    argv = rclone_calls[0]
    assert argv[argv.index("--config") + 1] == "/usr/local/etc/backup/rclone.conf"


async def test_a_share_that_will_not_take_the_file_is_reported_not_swallowed(
    harness: BotHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return FakeProcess(returncode=1, stderr=b"mount not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec)
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)

    with pytest.raises(ShareUnavailable, match="mount not found"):
        await build_delivery(harness, max_chat_mb=1).deliver(clip, chat_id=42, caption="x")


async def test_an_abandoned_copy_does_not_leave_rclone_running(
    harness: BotHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess()

    async def never_finishes() -> tuple[bytes, bytes]:
        await asyncio.sleep(3600)
        return b"", b""

    process.communicate = never_finishes  # type: ignore[method-assign]

    async def hanging_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", hanging_exec)
    clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)
    delivery = build_delivery(harness, max_chat_mb=1)

    task = asyncio.create_task(delivery.deliver(clip, chat_id=42, caption="x"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed


class TestShareName:
    """The name is the share's only index — there is no retention, no listing UI."""

    def test_it_sorts_by_date_and_greps_by_author_and_tweet(self, tmp_path: Path) -> None:
        clip = make_clip(
            tmp_path, tweet_id="1234567890", uploader="someone", upload_date=date(2026, 8, 13)
        )
        assert share_name(clip) == "2026-08-13-someone-1234567890.mp4"

    def test_several_clips_from_one_tweet_do_not_overwrite_each_other(self, tmp_path: Path) -> None:
        clip = make_clip(tmp_path)
        assert share_name(clip, index=2, total=2).endswith("-2.mp4")

    def test_a_hostile_handle_cannot_escape_the_file_name(self, tmp_path: Path) -> None:
        clip = make_clip(tmp_path, uploader="../../etc/passwd")
        name = share_name(clip)
        assert "/" not in name and ".." not in name


class TestFailuresThatMustNotLookGeneric:
    async def test_a_missing_rclone_is_reported_as_a_share_problem(
        self, harness: BotHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The likeliest first failure on a fresh server: rclone absent from the
        # supervisor's PATH. It must not surface as the generic "download
        # failed", which sends the operator looking in the wrong place.
        async def missing(*argv: str, **kwargs: Any) -> FakeProcess:
            raise FileNotFoundError(2, "No such file or directory", "rclone")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
        clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)

        with pytest.raises(ShareUnavailable, match="rclone"):
            await build_delivery(harness, max_chat_mb=1).deliver(clip, chat_id=42, caption="x")


class TestUploadDetails:
    async def test_an_overlong_caption_does_not_cost_a_downloaded_clip(
        self, harness: BotHarness, tmp_path: Path
    ) -> None:
        # Tweet URLs carry arbitrarily long tracking tails, aiogram does not
        # check the length, and Telegram answers 400 — losing a clip that
        # downloaded perfectly well.
        clip = make_clip(tmp_path, size_bytes=1024)
        caption = "https://x.com/a/status/1?ref=" + "z" * 2000

        await build_delivery(harness).deliver(clip, chat_id=42, caption=caption)

        sent = harness.session.calls_of(SendVideo)[0]
        assert sent.caption is not None
        assert len(sent.caption) <= 1024

    async def test_uploads_get_their_own_generous_timeout(
        self, harness: BotHarness, tmp_path: Path
    ) -> None:
        # The session-wide default is 15s, which tens of megabytes through a
        # proxy will never meet.
        clip = make_clip(tmp_path, size_bytes=1024)

        await build_delivery(harness).deliver(clip, chat_id=42, caption="x")

        assert harness.session.timeout_of(SendVideo) == 600

    async def test_the_copy_leaves_no_partial_file_on_a_share_nobody_prunes(
        self, harness: BotHarness, tmp_path: Path, rclone_calls: list[list[str]]
    ) -> None:
        clip = make_clip(tmp_path, size_bytes=2 * 1024 * 1024)

        await build_delivery(harness, max_chat_mb=1).deliver(clip, chat_id=42, caption="x")

        assert "--inplace" in rclone_calls[0]
