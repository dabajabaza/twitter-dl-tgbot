"""The vocabulary the rest of the code passes around.

Deliberately free of both aiogram and yt-dlp: the worker and the delivery route
speak in these terms, so neither has to import a download engine to be built or
exercised. See CONTEXT.md for what the words mean.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DownloadProgress:
    """One progress reading of one downloading file, as a person reads it.

    Clips often arrive as two separate files — the video stream and the audio
    stream, merged afterwards — and yt-dlp reports each on its own, so the
    percentage honestly runs to 100 twice. ``stream`` names which run this is,
    letting the status message say so instead of looking like a restart.
    """

    text: str  # "47% of 82 MB", or "12.3 MB" when the total is unknown
    stream: str = ""  # "video" / "audio" for separate streams, "" for one file


# Progress is pre-formatted for humans, not measured for machines: the only
# consumer is a status message.
ProgressCallback = Callable[[DownloadProgress], None]


@dataclass(frozen=True)
class Clip:
    """One downloaded video file, plus what is needed to name it externally."""

    path: Path
    post_id: str
    uploader: str
    upload_date: date
    # The post's own text, plain, cleaned of whatever trailing link furniture
    # the Provider appends to it.
    description: str = ""
    # The author's profile link, for the delivery caption's footer.
    uploader_url: str = ""


class Downloader(Protocol):
    """What the worker needs from a download engine.

    Stated here rather than beside the worker because it now has two sides to
    honour: the worker consumes one, and every Provider promises one. That makes
    it vocabulary — and vocabulary that names no engine, so neither the worker
    nor a Provider has to import yt-dlp to be built or exercised.
    """

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        on_progress: ProgressCallback | None = None,
        max_bytes: int | None = None,
    ) -> list[Clip]: ...
