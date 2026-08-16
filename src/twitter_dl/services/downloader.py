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
import threading
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
    DownloadTooLarge,
    NetworkUnavailable,
    NoVideoInTweet,
    TweetUnavailable,
)
from twitter_dl.services.cookies import CookieSession

logger = logging.getLogger(__name__)

# Only X. A tweet with no media but an outbound link makes yt-dlp follow that
# link (`url_result(expanded_url…)` in the extractor) and download whatever
# lives there — so a link to an article would come back as somebody else's
# video, captioned with the tweet and filed externally under their name.
_ALLOWED_EXTRACTORS = ["twitter.*"]

# yt-dlp appends a generic "how to pass cookies" hint to *every* login-required
# error. That hint alone contains the words that otherwise mean "our session was
# rejected", so it is cut off before anything is matched — otherwise a protected
# account nobody could see would read as expired cookies.
_LOGIN_HINT_MARKERS = ("use --cookies", "--cookies-from-browser")

# The account or tweet is in a state no credential would change. Checked before
# the auth markers, because X phrases these as authorization failures too:
# "You are not authorized to view this protected tweet" is not our session's
# fault, and waking the owner over it would devalue the one alert that matters.
_ACCOUNT_STATE_MARKERS = (
    "protected",
    "suspended",
    "deleted",
    "no longer exists",
    "does not exist",
    "doesn't exist",
    "not found",
)
# Our session specifically was not accepted.
_AUTH_MARKERS = (
    "nsfw",
    "requires authentication",
    "only available for registered users",
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
_NO_VIDEO_MARKERS = (
    "no video could be found",
    "no video",
    "is not a video",
    "no media",
    "unsupported url",
    # What the extractor restriction above produces when a tweet's only media
    # lives on someone else's site.
    "no suitable extractor",
)
_UNAVAILABLE_MARKERS = ("unavailable", "private")
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


class _Abandoned(Exception):
    """Raised inside the worker thread to unwind yt-dlp after a request is dropped."""


class _MaxFileSize(int):
    """Turn yt-dlp's Content-Length comparison into our typed early exit."""

    _limit_hit: list[int]

    def __new__(cls, limit: int, limit_hit: list[int]) -> "_MaxFileSize":
        value = super().__new__(cls, limit)
        value._limit_hit = limit_hit
        return value

    def __lt__(self, observed: object) -> bool:
        if isinstance(observed, int | float) and observed > int(self):
            self._limit_hit.append(int(observed))
            raise _Abandoned
        if isinstance(observed, int | float):
            return int(self) < observed
        return NotImplemented


class YtDlpDownloader:
    """Downloads every clip of a tweet at the best quality available."""

    def __init__(
        self,
        *,
        cookies: CookieSession | None = None,
        proxy: str | None = None,
    ) -> None:
        self._cookies = cookies
        self._proxy = proxy

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        on_progress: ProgressCallback | None = None,
        max_bytes: int | None = None,
    ) -> list[Clip]:
        """Fetch the tweet's clips into ``dest``.

        Runs the (synchronous) extractor off the event loop, and marshals its
        progress callbacks — which fire on that worker thread — back onto the
        loop, so callers may touch the Bot API from ``on_progress`` safely.
        """
        loop = asyncio.get_running_loop()
        abandoned = threading.Event()
        limit_hit: list[int] = []
        stream_bytes: dict[tuple[object, object], int] = {}
        clip_bytes: dict[object, int] = {}

        def hook(status: dict[str, Any]) -> None:
            if abandoned.is_set():
                raise _Abandoned
            if max_bytes is not None and status.get("status") == "downloading":
                downloaded = int(status.get("downloaded_bytes") or 0)
                clip, stream = _progress_keys(status)
                stream_key = (clip, stream)
                previous = stream_bytes.get(stream_key, 0)
                received = clip_bytes.get(clip, 0)
                received += downloaded - previous if downloaded >= previous else downloaded
                stream_bytes[stream_key] = downloaded
                clip_bytes[clip] = received

                observed = received
                exact = status.get("total_bytes")
                if isinstance(exact, int | float):
                    observed = max(observed, received - downloaded + int(exact))
                if observed > max_bytes:
                    limit_hit.append(int(observed))
                    raise _Abandoned
            if on_progress is None:
                return
            text = _format_progress(status)
            if text is not None:
                loop.call_soon_threadsafe(on_progress, text)

        try:
            info = await asyncio.to_thread(
                self._extract,
                url,
                dest,
                hook,
                abandoned,
                max_bytes,
                limit_hit,
            )
        except asyncio.CancelledError:
            # A running thread cannot be cancelled: `to_thread`'s future is
            # already RUNNING, so cancel() returns False and yt-dlp keeps
            # downloading long after the request was abandoned — holding the
            # uplink, and blocking shutdown on the executor join. Raising from
            # the next progress callback is the one way in: the thread unwinds
            # itself within a chunk or two.
            abandoned.set()
            raise
        except Exception as exc:
            if limit_hit:
                raise DownloadTooLarge(
                    limit_bytes=max_bytes or 0,
                    observed_bytes=limit_hit[0],
                ) from exc
            if isinstance(exc, DownloadError | ExtractorError):
                raise _classify(exc) from exc
            raise
        if limit_hit:
            raise DownloadTooLarge(
                limit_bytes=max_bytes or 0,
                observed_bytes=limit_hit[0],
            )
        return _clips_from_info(info, url)

    def _extract(
        self,
        url: str,
        dest: Path,
        hook: Callable[[dict[str, Any]], None],
        abandoned: threading.Event,
        max_bytes: int | None,
        limit_hit: list[int],
    ) -> Any:
        try:
            with YoutubeDL(
                self._options(dest, hook, max_bytes=max_bytes, limit_hit=limit_hit)
            ) as ydl:
                return ydl.extract_info(url, download=True)
        except Exception:
            # Whatever yt-dlp wrapped our _Abandoned in, the request is already
            # gone and nobody is waiting for this result.
            if abandoned.is_set():
                logger.info("abandoned download of %s unwound in its thread", url)
                return None
            raise

    def _options(
        self,
        dest: Path,
        hook: Callable[[dict[str, Any]], None],
        *,
        max_bytes: int | None = None,
        limit_hit: list[int] | None = None,
    ) -> dict[str, Any]:
        hits = limit_hit if limit_hit is not None else []

        def reject_known_oversize(info: dict[str, Any], *, incomplete: bool) -> None:
            if incomplete or max_bytes is None:
                return
            exact = _exact_selected_size(info)
            if exact is not None and exact > max_bytes:
                hits.append(exact)
                raise _Abandoned

        options: dict[str, Any] = {
            # yt-dlp's own default selector. The `/b` fallback is what carries
            # X's animated GIFs, which are audio-less mp4 and so never satisfy
            # the `bv*+ba` half.
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": {"default": "%(id)s.%(ext)s"},
            "paths": {"home": str(dest)},
            "allowed_extractors": _ALLOWED_EXTRACTORS,
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
        if max_bytes is not None:
            # The format metadata check runs before any downloader is opened;
            # max_filesize catches an exact HTTP Content-Length before its body.
            options["match_filter"] = reject_known_oversize
            options["max_filesize"] = _MaxFileSize(max_bytes, hits)
        # A throwaway copy inside this request's own scratch directory, never
        # the owner's export: yt-dlp rewrites the cookie file it is given on
        # every run, including on the way out of an abandoned download (see
        # services/cookies.py).
        cookiefile = self._cookies.stage_into(dest) if self._cookies else None
        if cookiefile is not None:
            options["cookiefile"] = str(cookiefile)
        if self._proxy is not None:
            options["proxy"] = self._proxy
        return options


def _exact_selected_size(info: dict[str, Any]) -> int | None:
    formats = info.get("requested_formats")
    if formats:
        sizes = [item.get("filesize") for item in formats]
        if sizes and all(isinstance(size, int | float) for size in sizes):
            return sum(int(size) for size in sizes)
        return None
    size = info.get("filesize")
    return int(size) if isinstance(size, int | float) else None


def _progress_keys(status: dict[str, Any]) -> tuple[object, object]:
    info = status.get("info_dict")
    if not isinstance(info, dict):
        return "download", status.get("filename") or status.get("tmpfilename") or "stream"
    clip = (info.get("playlist_index"), info.get("id") or info.get("display_id"))
    stream = info.get("format_id") or status.get("filename") or status.get("tmpfilename")
    return clip, stream or id(info)


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
        tweet_id=_tweet_id(entry),
        uploader=str(entry.get("uploader_id") or entry.get("uploader") or "unknown"),
        upload_date=_upload_date(entry),
    )


def _tweet_id(entry: dict[str, Any]) -> str:
    """The id from the link, not the id of the media inside it.

    The X extractor puts the media object's id in `id` and the tweet's own id in
    `display_id`. External names must be discoverable from the original link
    (ARCHITECTURE.md D7), or they are not a useful index.
    """
    return str(entry.get("display_id") or entry.get("id") or "unknown")


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


def _strip_login_hint(text: str) -> str:
    """Drop yt-dlp's boilerplate advice about passing cookies.

    It is appended to every login-required error and says nothing about *this*
    failure, but it does contain the words the auth markers look for.
    """
    lowered = text.lower()
    for marker in _LOGIN_HINT_MARKERS:
        index = lowered.find(marker)
        if index != -1:
            text = text[:index]
            lowered = text.lower()
    return text


def _classify(exc: Exception) -> Exception:
    """Map yt-dlp's one exception type onto the failure taxonomy."""
    detail = str(exc)
    text = _strip_login_hint(detail).lower()
    if any(marker in text for marker in _NETWORK_MARKERS):
        return NetworkUnavailable(detail)
    if any(marker in text for marker in _ACCOUNT_STATE_MARKERS):
        return TweetUnavailable(detail)
    if any(marker in text for marker in _AUTH_MARKERS):
        return AuthExpired(detail)
    if any(marker in text for marker in _NO_VIDEO_MARKERS):
        return NoVideoInTweet(detail)
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return TweetUnavailable(detail)
    return DownloadFailed(detail)


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
