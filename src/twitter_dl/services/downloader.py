"""Pulling clips out of a tweet with yt-dlp.

yt-dlp is used as a library rather than a subprocess for two reasons that both
show up in the chat: progress arrives as structured callbacks instead of text
to be scraped off stdout, and failures arrive as an exception whose message can
be classified once, here, instead of at every call site.

One tweet can hold several videos, so a download yields a *list* of clips.
"""

import asyncio
import logging
import re
import shutil
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from twitter_dl.domain import Clip, ProgressCallback
from twitter_dl.errors import (
    AuthExpired,
    DownloadFailed,
    NetworkUnavailable,
    NoVideoInTweet,
    TweetUnavailable,
)

logger = logging.getLogger(__name__)

# Substrings X and yt-dlp actually produce, grouped by what the operator should
# do about them. Matched case-insensitively against the whole exception text.
# Order matters: the first group that matches wins, and auth is checked first
# because an expired session often presents as "not available" too.
_AUTH_MARKERS = (
    "nsfw",
    "log in",
    "login",
    "sign in",
    "logged in",
    "authoriz",
    "authenticat",
    "cookies",
    "account is required",
    "age-restricted",
    "age restricted",
)
_UNAVAILABLE_MARKERS = (
    "no longer exists",
    "does not exist",
    "not found",
    "unavailable",
    "suspended",
    "protected",
    "deleted",
    "private",
)
_NO_VIDEO_MARKERS = (
    "no video could be found",
    "no video",
    "there's no video",
    "no media",
    "unsupported url",
)
_NETWORK_MARKERS = (
    "proxy",
    "connection refused",
    "connection reset",
    "unable to connect",
    "failed to resolve",
    "name resolution",
    "timed out",
    "timeout",
    "network is unreachable",
    "tunnel connection failed",
)


class YtDlpDownloader:
    """Downloads every clip of a tweet at the best quality available."""

    def __init__(
        self,
        *,
        cookies_file: Path | None = None,
        proxy: str | None = None,
    ) -> None:
        self._cookies_file = cookies_file
        self._proxy = proxy

    async def download(
        self, url: str, dest: Path, *, on_progress: ProgressCallback | None = None
    ) -> list[Clip]:
        """Fetch the tweet's clips into ``dest``.

        Runs the (synchronous) extractor off the event loop, and marshals its
        progress callbacks — which fire on that worker thread — back onto the
        loop, so callers may touch the Bot API from ``on_progress`` safely.
        """
        loop = asyncio.get_running_loop()

        def hook(status: dict[str, Any]) -> None:
            if on_progress is None:
                return
            text = _format_progress(status)
            if text is not None:
                loop.call_soon_threadsafe(on_progress, text)

        try:
            info = await asyncio.to_thread(self._extract, url, dest, hook)
        except (DownloadError, ExtractorError) as exc:
            raise _classify(exc) from exc
        return _clips_from_info(info, url)

    def _extract(self, url: str, dest: Path, hook: Callable[[dict[str, Any]], None]) -> Any:
        with YoutubeDL(self._options(dest, hook)) as ydl:
            return ydl.extract_info(url, download=True)

    def _options(self, dest: Path, hook: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        options: dict[str, Any] = {
            # yt-dlp's own default selector. The `/b` fallback is what carries
            # X's animated GIFs, which are audio-less mp4 and so never satisfy
            # the `bv*+ba` half.
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "paths": {"home": str(dest)},
            # A tweet holding several videos is a playlist to yt-dlp, and all of
            # them are wanted.
            "noplaylist": False,
            "restrictfilenames": True,
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "logger": _YtdlpLogger(),
            "progress_hooks": [hook],
            "socket_timeout": 30,
            "retries": 3,
        }
        if self._cookies_file is not None:
            options["cookiefile"] = str(self._cookies_file)
        if self._proxy is not None:
            options["proxy"] = self._proxy
        return options


def ffmpeg_available() -> bool:
    """Whether merging separate video and audio streams is possible.

    Without ffmpeg yt-dlp silently falls back to a single progressive stream,
    which on X means capping quality below what the account can actually see —
    exactly the complaint this bot exists to answer.
    """
    return shutil.which("ffmpeg") is not None


def _format_progress(status: dict[str, Any]) -> str | None:
    if status.get("status") != "downloading":
        return None
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    done = status.get("downloaded_bytes") or 0
    if not total:
        return f"{done / 1024 / 1024:.1f} MB"
    return f"{done * 100 / total:.0f}%"


def _clips_from_info(info: Any, url: str) -> list[Clip]:
    if info is None:
        raise NoVideoInTweet(f"nothing to download at {url}")
    entries = info["entries"] if info.get("_type") == "playlist" else [info]
    clips = [clip for entry in entries if entry and (clip := _clip_from_entry(entry, url))]
    if not clips:
        raise NoVideoInTweet(f"nothing to download at {url}")
    return clips


def _clip_from_entry(entry: dict[str, Any], url: str) -> Clip | None:
    path = _downloaded_path(entry)
    if path is None or not path.exists():
        logger.warning("entry of %s reported no file on disk", url)
        return None
    return Clip(
        path=path,
        tweet_id=str(entry.get("id") or "unknown"),
        uploader=str(entry.get("uploader_id") or entry.get("uploader") or "unknown"),
        upload_date=_upload_date(entry),
    )


def _downloaded_path(entry: dict[str, Any]) -> Path | None:
    """Where the file actually landed after any merge or remux."""
    for download in entry.get("requested_downloads") or ():
        filepath = download.get("filepath")
        if filepath:
            return Path(filepath)
    filename = entry.get("filepath") or entry.get("_filename")
    return Path(filename) if filename else None


def _upload_date(entry: dict[str, Any]) -> date:
    raw = entry.get("upload_date")
    if raw:
        try:
            return datetime.strptime(str(raw), "%Y%m%d").date()
        except ValueError:
            logger.warning("unparseable upload_date %r", raw)
    return date.today()


def _classify(exc: Exception) -> Exception:
    """Map yt-dlp's one exception type onto the failure taxonomy."""
    text = str(exc).lower()
    if any(marker in text for marker in _AUTH_MARKERS):
        return AuthExpired(str(exc))
    if any(marker in text for marker in _NETWORK_MARKERS):
        return NetworkUnavailable(str(exc))
    if any(marker in text for marker in _NO_VIDEO_MARKERS):
        return NoVideoInTweet(str(exc))
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return TweetUnavailable(str(exc))
    return DownloadFailed(str(exc))


class _YtdlpLogger:
    """Routes yt-dlp's chatter into the application log instead of stdout."""

    # yt-dlp emits ANSI-coloured, sometimes multi-line strings; the log is read
    # through /var/log/messages where escapes are noise.
    _ANSI = re.compile(r"\x1b\[[0-9;]*m")

    def debug(self, msg: str) -> None:
        logger.debug("%s", self._ANSI.sub("", msg))

    def info(self, msg: str) -> None:
        logger.debug("%s", self._ANSI.sub("", msg))

    def warning(self, msg: str) -> None:
        logger.warning("%s", self._ANSI.sub("", msg))

    def error(self, msg: str) -> None:
        # Not logged at error level: yt-dlp raises DownloadError for anything
        # that actually failed, and the worker logs that with its verdict.
        logger.warning("%s", self._ANSI.sub("", msg))
