"""The two built-in destinations use rclone and return human locators."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from twitter_dl.adapters import _rclone
from twitter_dl.adapters.share import ShareDestination, ShareSettings
from twitter_dl.adapters.yandex_disk import YandexDiskDestination, YandexDiskSettings


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


@pytest.fixture
def rclone_calls(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], list[FakeProcess]]:
    calls: list[list[str]] = []
    processes = [FakeProcess(), FakeProcess(stdout=b"https://disk.yandex.example/public\n")]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        calls.append(list(argv))
        return processes[len(calls) - 1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls, processes


def share_settings(tmp_path: Path) -> ShareSettings:
    config = tmp_path / "rclone.conf"
    config.write_text("[share]\n")
    return ShareSettings(
        rclone_binary="/bin/true",
        rclone_config=config,
        rclone_remote="share:twitter-dl",
        path_prefix=r"\\router\twitter-dl",
        _env_file=None,
    )


def yandex_settings(tmp_path: Path) -> YandexDiskSettings:
    config = tmp_path / "rclone.conf"
    config.write_text("[yandex]\n")
    return YandexDiskSettings(
        rclone_binary="/bin/true",
        rclone_config=config,
        rclone_remote="yandex:twitter-dl",
        _env_file=None,
    )


@pytest.mark.parametrize("remote", ["", "   ", "not-a-remote", ":path", r"bad/name:path"])
def test_rclone_destination_must_name_a_remote(tmp_path: Path, remote: str) -> None:
    with pytest.raises(ValidationError):
        YandexDiskSettings(
            rclone_binary="/bin/true",
            rclone_remote=remote,
            _env_file=None,
        )


def test_share_human_path_must_not_be_empty(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ShareSettings(
            rclone_binary="/bin/true",
            rclone_remote="share:path",
            path_prefix="  ",
            _env_file=None,
        )


async def test_share_returns_the_configured_human_path(
    tmp_path: Path, rclone_calls: tuple[list[list[str]], list[FakeProcess]]
) -> None:
    calls, _ = rclone_calls
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")

    location = await ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")

    assert location == r"\\router\twitter-dl\named.mp4"
    assert "copyto" in calls[0]
    assert "--inplace" in calls[0]
    assert calls[0][-1] == "share:twitter-dl/named.mp4"


async def test_yandex_uploads_then_returns_a_public_link(
    tmp_path: Path, rclone_calls: tuple[list[list[str]], list[FakeProcess]]
) -> None:
    calls, _ = rclone_calls
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")

    location = await YandexDiskDestination(yandex_settings(tmp_path)).store(
        source, name="named.mp4"
    )

    assert location == "https://disk.yandex.example/public"
    assert "copyto" in calls[0]
    assert "link" in calls[1]
    assert calls[0][-1] == calls[1][-1] == "yandex:twitter-dl/named.mp4"


async def test_a_cancelled_rclone_process_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess()

    async def never_finishes() -> tuple[bytes, bytes]:
        await asyncio.sleep(3600)
        return b"", b""

    process.communicate = never_finishes  # type: ignore[method-assign]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    task = asyncio.create_task(
        ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")
    )
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed


async def test_cancellation_survives_a_process_that_already_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess()
    waited = False

    async def cancelled_communicate() -> tuple[bytes, bytes]:
        raise asyncio.CancelledError

    def already_exited() -> None:
        raise ProcessLookupError

    async def record_wait() -> int:
        nonlocal waited
        waited = True
        return 0

    process.communicate = cancelled_communicate  # type: ignore[method-assign]
    process.kill = already_exited  # type: ignore[method-assign]
    process.wait = record_wait  # type: ignore[method-assign]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")

    with pytest.raises(asyncio.CancelledError):
        await ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")
    assert waited


async def test_a_communicate_failure_still_kills_and_reaps_rclone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess()

    async def pipe_failed() -> tuple[bytes, bytes]:
        raise OSError("pipe failed")

    process.communicate = pipe_failed  # type: ignore[method-assign]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")

    with pytest.raises(OSError, match="pipe failed"):
        await ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")
    assert process.killed
    assert process.waited


async def test_repeated_cancellation_waits_until_rclone_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess()
    communicating = asyncio.Event()
    wait_started = asyncio.Event()
    release_wait = asyncio.Event()
    wait_cancelled = False
    wait_completed = False

    async def blocked_communicate() -> tuple[bytes, bytes]:
        communicating.set()
        await asyncio.sleep(3600)
        return b"", b""

    async def blocked_wait() -> int:
        nonlocal wait_cancelled, wait_completed
        wait_started.set()
        try:
            await release_wait.wait()
        except asyncio.CancelledError:
            wait_cancelled = True
            raise
        wait_completed = True
        return 0

    process.communicate = blocked_communicate  # type: ignore[method-assign]
    process.wait = blocked_wait  # type: ignore[method-assign]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    task = asyncio.create_task(
        ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")
    )
    await communicating.wait()
    task.cancel()
    await wait_started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    release_wait.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
    assert wait_completed
    assert not wait_cancelled


@pytest.mark.parametrize("failure", [asyncio.CancelledError, OSError])
async def test_a_kill_failure_does_not_replace_the_original_error_or_skip_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    process = FakeProcess()

    async def failing_communicate() -> tuple[bytes, bytes]:
        raise failure("rclone failed")

    def kill_denied() -> None:
        raise PermissionError("kill denied")

    process.communicate = failing_communicate  # type: ignore[method-assign]
    process.kill = kill_denied  # type: ignore[method-assign]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")

    with pytest.raises(failure, match="rclone failed"):
        await ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")
    assert process.waited


@pytest.mark.parametrize("failure", [asyncio.CancelledError, OSError])
async def test_an_unreapable_rclone_is_abandoned_instead_of_waited_on_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    process = FakeProcess()
    never_exits = asyncio.Event()

    async def failing_communicate() -> tuple[bytes, bytes]:
        raise failure("rclone failed")

    async def stuck_wait() -> int:
        await never_exits.wait()
        return 0

    process.communicate = failing_communicate  # type: ignore[method-assign]
    process.wait = stuck_wait  # type: ignore[method-assign]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(_rclone, "_REAP_TIMEOUT_S", 0.01)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    task = asyncio.create_task(
        ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")
    )

    done, _ = await asyncio.wait({task}, timeout=1)

    assert done, "waited for a process that never exits"
    with pytest.raises(failure, match="rclone failed"):
        await task
    never_exits.set()
    await asyncio.sleep(0)


async def test_cancellation_during_cleanup_wins_over_an_ordinary_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess()
    wait_started = asyncio.Event()
    release_wait = asyncio.Event()
    wait_cancelled = False

    async def pipe_failed() -> tuple[bytes, bytes]:
        raise OSError("pipe failed")

    async def blocked_wait() -> int:
        nonlocal wait_cancelled
        wait_started.set()
        try:
            await release_wait.wait()
        except asyncio.CancelledError:
            wait_cancelled = True
            raise
        return 0

    process.communicate = pipe_failed  # type: ignore[method-assign]
    process.wait = blocked_wait  # type: ignore[method-assign]

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    task = asyncio.create_task(
        ShareDestination(share_settings(tmp_path)).store(source, name="named.mp4")
    )
    await wait_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release_wait.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert not wait_cancelled
