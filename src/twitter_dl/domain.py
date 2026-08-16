"""The vocabulary the rest of the code passes around.

Deliberately free of both aiogram and yt-dlp: the worker and the delivery route
speak in these terms, so neither has to import a download engine to be built or
exercised. See CONTEXT.md for what the words mean.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Progress as a person reads it ("47%", "12.3 MB"), not as a machine measures it:
# the only consumer is a status message.
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class Clip:
    """One downloaded video file, plus what is needed to name it externally."""

    path: Path
    tweet_id: str
    uploader: str
    upload_date: date
