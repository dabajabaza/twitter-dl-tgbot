"""The vocabulary the rest of the code passes around.

Deliberately free of both aiogram and yt-dlp: the worker and the delivery route
speak in these terms, so neither has to import a download engine to be built or
exercised. See CONTEXT.md for what the words mean.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class DownloadProgress:
    """One progress reading of one downloading file, as a person reads it.

    X clips usually arrive as two separate files — the video stream and the
    audio stream, merged afterwards — and yt-dlp reports each on its own, so
    the percentage honestly runs to 100 twice. ``stream`` names which run this
    is, letting the status message say so instead of looking like a restart.
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
    tweet_id: str
    uploader: str
    upload_date: date
    # The tweet's own text, plain, with the trailing t.co media pointer stripped.
    description: str = ""
    # The author's profile link, for the delivery caption's footer.
    uploader_url: str = ""
