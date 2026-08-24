"""Pulling clips out of a post with yt-dlp.

yt-dlp is used as a library rather than a subprocess for two reasons that both
show up in the chat: progress arrives as structured callbacks instead of text
to be scraped off stdout, and failures arrive as an exception whose message can
be classified once, here, instead of at every call site.

One post can hold several videos, so a download yields a *list* of clips.

This is still the only module that imports yt-dlp (ARCHITECTURE.md D12). What
differs between platforms — which extractors may run, which sentences mean what,
how the metadata is spelled — arrives as an `EngineProfile` from the Provider,
so a second platform costs a declaration rather than a second engine.
"""

import asyncio
import logging
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from clipivore.domain import Clip, DownloadProgress, ProgressCallback
from clipivore.errors import (
    AuthExpired,
    DownloadFailed,
    DownloadTooLarge,
    NetworkUnavailable,
    NoVideoInPost,
    PostUnavailable,
)
from clipivore.services.cookies import CookieSession

logger = logging.getLogger(__name__)

# yt-dlp appends a generic "how to pass cookies" hint to *every* login-required
# error. That hint alone contains the words that otherwise mean "our session was
# rejected", so it is cut off before anything is matched — otherwise a protected
# account nobody could see would read as expired cookies.
_LOGIN_HINT_MARKERS = ("use --cookies", "--cookies-from-browser")

# Not per-platform: these are the engine's own failures, in the engine's own
# words. The network is the network whoever is on the other end.
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
# Produced by the extractor allowlist below when a post's only media lives on
# somebody else's site. Every Provider gets this one for free, because every
# Provider has the allowlist.
_ENGINE_NO_VIDEO_MARKERS = ("unsupported url", "no suitable extractor")


def _unchanged(text: str) -> str:
    return text


def _no_profile(handle: str) -> str:
    return ""


@dataclass(frozen=True)
class EngineProfile:
    """What one platform needs from the shared yt-dlp engine.

    ``allowed_extractors`` is a scope guard, not a convenience (ARCHITECTURE.md
    D15): a post with no media but an outbound link makes yt-dlp follow that
    link — `url_result(...)` inside the extractor — and download whatever lives
    there. Without the lock a link to an article comes back as somebody else's
    video, captioned with this post and filed externally under their name. It is
    always one platform's extractors, never the union of every Provider's: a
    union would let one platform's post redirect into another's extractor.
    """

    allowed_extractors: tuple[str, ...]
    # Checked before the auth markers: a state no credential would change.
    # Platforms phrase these as authorization failures too, and waking the owner
    # over a protected account would devalue the one alert that matters.
    account_state_markers: tuple[str, ...] = ()
    # This session specifically was not accepted.
    auth_markers: tuple[str, ...] = ()
    no_video_markers: tuple[str, ...] = ()
    unavailable_markers: tuple[str, ...] = ()
    # Strips whatever link furniture the platform appends to a post's text.
    clean_description: Callable[[str], str] = field(default=_unchanged)
    # The author's profile URL, when the extractor did not supply one.
    profile_url: Callable[[str], str] = field(default=_no_profile)


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
    """Downloads every clip of a post at the best quality available."""

    def __init__(
        self,
        profile: EngineProfile,
        *,
        cookies: CookieSession | None = None,
        proxy: str | None = None,
    ) -> None:
        self._profile = profile
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
        """Fetch the post's clips into ``dest``.

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
            progress = _format_progress(status)
            if progress is not None:
                loop.call_soon_threadsafe(on_progress, progress)

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
                raise _classify(exc, self._profile) from exc
            raise
        if limit_hit:
            raise DownloadTooLarge(
                limit_bytes=max_bytes or 0,
                observed_bytes=limit_hit[0],
            )
        return _clips_from_info(info, url, self._profile)

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
            "allowed_extractors": list(self._profile.allowed_extractors),
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


def _format_progress(status: dict[str, Any]) -> DownloadProgress | None:
    if status.get("status") != "downloading":
        return None
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    done = status.get("downloaded_bytes") or 0
    if not total:
        text = f"{done / 1024 / 1024:.1f} MB"
    else:
        text = f"{done * 100 / total:.0f}% of {_human_size(total)}"
    return DownloadProgress(text=text, stream=_stream_kind(status))


def _stream_kind(status: dict[str, Any]) -> str:
    """Which half of a two-file download this is, if it is one.

    With the ``bv*+ba`` selector the video and audio streams download as two
    files and the hook reports each separately; the single-file ``/b``
    fallback (X GIFs, no ffmpeg) carries both codecs and gets no name.

    The audio half is recognised by ``vcodec == "none"`` alone: X's audio
    renditions come from an HLS ``EXT-X-MEDIA`` entry, for which yt-dlp fills
    ``vcodec`` but never sets ``acodec`` at all — keying on ``acodec`` would
    leave the second run of the percentage nameless on every real clip.
    """
    info = status.get("info_dict") or {}
    vcodec = info.get("vcodec")
    acodec = info.get("acodec")
    if vcodec == "none":
        return "audio"
    if vcodec and acodec == "none":
        return "video"
    return ""


def _human_size(size_bytes: float) -> str:
    """Mirror of bot/texts.human_size: a service cannot import the bot layer."""
    kilobytes = size_bytes / 1024
    megabytes = kilobytes / 1024
    if megabytes >= 1024:
        return f"{megabytes / 1024:.1f} GB"
    if megabytes >= 1:
        return f"{megabytes:.0f} MB"
    # Audio streams run to a few hundred KB; "0 MB" would read as a glitch.
    return f"{kilobytes:.0f} KB"


def _clips_from_info(info: Any, url: str, profile: EngineProfile) -> list[Clip]:
    if info is None:
        raise NoVideoInPost(f"nothing to download at {url}")
    entries = info["entries"] if info.get("_type") == "playlist" else [info]
    clips = [clip for entry in entries if entry and (clip := _clip_from_entry(entry, url, profile))]
    if not clips:
        raise NoVideoInPost(f"nothing to download at {url}")
    return clips


def _clip_from_entry(entry: dict[str, Any], url: str, profile: EngineProfile) -> Clip | None:
    path = _downloaded_path(entry)
    if path is None or not path.exists():
        logger.warning("entry of %s reported no file on disk", url)
        return None
    return Clip(
        path=path,
        post_id=_post_id(entry),
        uploader=str(entry.get("uploader_id") or entry.get("uploader") or "unknown"),
        upload_date=_upload_date(entry),
        description=_description(entry, profile),
        uploader_url=_uploader_url(entry, profile),
    )


def _description(entry: dict[str, Any], profile: EngineProfile) -> str:
    """The post's text, cleaned of the platform's own link furniture."""
    raw = str(entry.get("description") or "")
    return profile.clean_description(raw).strip()


def _uploader_url(entry: dict[str, Any], profile: EngineProfile) -> str:
    url = entry.get("uploader_url")
    if url:
        return str(url)
    handle = entry.get("uploader_id")
    return profile.profile_url(str(handle)) if handle else ""


def _post_id(entry: dict[str, Any]) -> str:
    """The id from the link, not the id of the media inside it.

    The X extractor puts the media object's id in `id` and the post's own id in
    `display_id`; an extractor with nothing to disambiguate sets only `id`, and
    that is the post's. External names must be discoverable from the original
    link (ARCHITECTURE.md D7), or they are not a useful index.
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


def _classify(exc: Exception, profile: EngineProfile) -> Exception:
    """Map yt-dlp's one exception type onto the failure taxonomy.

    The order is the platform's, not the engine's: an account state is checked
    before authentication because platforms phrase "you may not see this" and
    "we do not know you" in the same words.
    """
    detail = str(exc)
    text = _strip_login_hint(detail).lower()
    if any(marker in text for marker in _NETWORK_MARKERS):
        return NetworkUnavailable(detail)
    if any(marker in text for marker in profile.account_state_markers):
        return PostUnavailable(detail)
    if any(marker in text for marker in profile.auth_markers):
        return AuthExpired(detail)
    if any(marker in text for marker in (*profile.no_video_markers, *_ENGINE_NO_VIDEO_MARKERS)):
        return NoVideoInPost(detail)
    if any(marker in text for marker in profile.unavailable_markers):
        return PostUnavailable(detail)
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
