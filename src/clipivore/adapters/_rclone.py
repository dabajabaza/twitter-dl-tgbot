"""The small rclone subprocess shared by the built-in destinations."""

import asyncio
import logging
import shutil
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# How long the cleanup path waits for a killed rclone to be reaped. SIGKILL
# normally lands within milliseconds, but rclone writing to a wedged SMB share
# sits in uninterruptible I/O and does not die until the mount answers. That
# wait deliberately absorbs cancellations, so without a bound one stuck process
# would swallow the download deadline and the shutdown signal along with it.
_REAP_TIMEOUT_S = 5.0


class RcloneSettings(BaseSettings):
    rclone_binary: str = "rclone"
    rclone_config: Path | None = None

    @field_validator("rclone_remote", check_fields=False)
    @classmethod
    def _valid_remote(cls, value: str) -> str:
        remote = value.strip().rstrip("/")
        name, separator, _ = remote.partition(":")
        if not separator or not name or "/" in name or "\\" in name:
            raise ValueError("rclone remote must have the form remote:path")
        return remote

    @field_validator("rclone_config", mode="before")
    @classmethod
    def _empty_path_means_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _rclone_is_runnable(self) -> "RcloneSettings":
        if shutil.which(self.rclone_binary) is None:
            raise ValueError(f"cannot find rclone executable {self.rclone_binary!r}")
        if self.rclone_config is not None and not self.rclone_config.is_file():
            raise ValueError(f"rclone config is not a file: {self.rclone_config}")
        return self


def _forget_late_exit(wait_task: asyncio.Task[int]) -> None:
    """Consume the outcome of a wait given up on, so asyncio does not complain."""
    if not wait_task.cancelled():
        wait_task.exception()


async def run_rclone(settings: RcloneSettings, *args: str) -> str:
    argv = [settings.rclone_binary]
    if settings.rclone_config is not None:
        argv += ["--config", str(settings.rclone_config)]
    argv += args

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate()
    except BaseException as original_error:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except BaseException as kill_error:
            logger.warning("could not kill failed rclone process: %s", kill_error)

        cleanup_cancelled: asyncio.CancelledError | None = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _REAP_TIMEOUT_S
        wait_task = asyncio.create_task(process.wait())
        while not wait_task.done() and (remaining := deadline - loop.time()) > 0:
            # asyncio.wait() neither cancels what it waits for nor re-raises its
            # exception, so this one task survives every round of the loop.
            try:
                await asyncio.wait({wait_task}, timeout=remaining)
            except asyncio.CancelledError as cancellation:
                if isinstance(original_error, Exception):
                    cleanup_cancelled = cancellation
        if not wait_task.done():
            logger.warning("gave up waiting for rclone to exit; it may still be running")
            wait_task.add_done_callback(_forget_late_exit)
        elif not wait_task.cancelled():
            try:
                wait_task.result()
            except Exception as cleanup_error:
                logger.warning("could not reap failed rclone process: %s", cleanup_error)
        if cleanup_cancelled is not None:
            raise cleanup_cancelled from original_error
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"rclone exited with {process.returncode}")
    return stdout.decode("utf-8", errors="replace").strip()
