"""Getting a downloaded clip to the person who asked for it.

Two destinations, chosen by size alone. Under the Bot API's upload ceiling the
clip goes into the chat, which is the whole point of the bot. Over it, Telegram
simply refuses, so the clip goes to the SMB share on the router and the chat
gets the path instead — the same answer for everyone, since teaching the bot who
is on the LAN would buy nothing (see docs/ARCHITECTURE.md D7).
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from twitter_dl.domain import Clip
from twitter_dl.errors import ShareUnavailable

logger = logging.getLogger(__name__)

# Uploading tens of megabytes through the proxy takes far longer than a normal
# API call, so this overrides the session-wide request timeout for that one call.
_UPLOAD_TIMEOUT_S = 600
# Bot API ceiling for a media caption, which aiogram does not enforce itself.
_CAPTION_LIMIT = 1024
# Dots are excluded along with the obvious separators: the metadata comes from
# X by way of yt-dlp, and a handle of "../.." must not be able to say anything
# about a path once it is pasted after the remote's name.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9_-]+")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}")


@dataclass(frozen=True)
class ChatDelivery:
    """The clip is in the chat."""

    size_bytes: int


@dataclass(frozen=True)
class ShareDelivery:
    """The clip is on the share, under this human-facing path."""

    size_bytes: int
    display_path: str


DeliveryResult = ChatDelivery | ShareDelivery


class ClipDelivery:
    """Routes each clip by size, and knows how to name it on the share."""

    def __init__(
        self,
        bot: Bot,
        *,
        max_chat_bytes: int,
        rclone_binary: str,
        rclone_config: Path | None,
        rclone_remote: str,
        share_path_prefix: str,
    ) -> None:
        self._bot = bot
        self._max_chat_bytes = max_chat_bytes
        self._rclone_binary = rclone_binary
        self._rclone_config = rclone_config
        self._rclone_remote = rclone_remote
        self._share_path_prefix = share_path_prefix

    async def deliver(
        self, clip: Clip, *, chat_id: int, caption: str, index: int = 1, total: int = 1
    ) -> DeliveryResult:
        size = clip.path.stat().st_size
        if size <= self._max_chat_bytes:
            await self._bot.send_video(
                chat_id=chat_id,
                video=FSInputFile(clip.path),
                caption=_fit_caption(caption),
                supports_streaming=True,
                request_timeout=_UPLOAD_TIMEOUT_S,
            )
            return ChatDelivery(size_bytes=size)

        name = share_name(clip, index=index, total=total)
        await self._copy_to_share(clip.path, name)
        return ShareDelivery(size_bytes=size, display_path=f"{self._share_path_prefix}\\{name}")

    async def _copy_to_share(self, source: Path, name: str) -> None:
        argv = [self._rclone_binary]
        if self._rclone_config is not None:
            argv += ["--config", str(self._rclone_config)]
        # copyto rather than copy: the destination is a full file path, and the
        # name on the share is ours, not the scratch file's.
        # --inplace: without it rclone writes "<name>.partial" and renames on
        # completion, so a copy killed on timeout leaves hundreds of megabytes
        # of rubbish on a share that has no retention policy at all.
        argv += [
            "--no-traverse",
            "--inplace",
            "copyto",
            str(source),
            f"{self._rclone_remote}/{name}",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            # rclone missing from the supervisor's PATH, or RCLONE_BINARY
            # mistyped — the likeliest first failure on a fresh server. Without
            # this it surfaces as the generic "download failed" instead of the
            # share-specific answer that says where to look.
            logger.error("could not run %s: %s", self._rclone_binary, exc)
            raise ShareUnavailable(f"cannot run {self._rclone_binary}: {exc}") from exc
        try:
            _, stderr = await process.communicate()
        except asyncio.CancelledError:
            # The request timed out or the bot is shutting down. Killing the
            # child is on us: nothing reaps it otherwise, and an rclone stuck on
            # a dead share would outlive every request that follows.
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            logger.error("rclone failed (%s): %s", process.returncode, message)
            raise ShareUnavailable(message or f"rclone exited with {process.returncode}")


def _utf16_length(text: str) -> int:
    """Length as Telegram counts it: UTF-16 code units, not code points.

    Anything outside the basic plane — an emoji, most notably — is two units to
    Telegram and one character to Python, so counting characters lets a caption
    Telegram considers too long slip through.
    """
    return len(text.encode("utf-16-le")) // 2


def _fit_caption(caption: str) -> str:
    """Keep the caption inside the Bot API's limit.

    aiogram does not check the length, so an over-long one comes back as a 400
    and loses a clip that downloaded perfectly well — and tweet URLs carry
    arbitrarily long tracking tails.
    """
    if _utf16_length(caption) <= _CAPTION_LIMIT:
        return caption
    # Trimmed one character at a time from the end rather than sliced by index:
    # a slice at a fixed offset can land between the halves of a surrogate pair
    # and produce a caption Telegram rejects outright.
    kept = caption
    while kept and _utf16_length(kept) > _CAPTION_LIMIT - 1:
        kept = kept[:-1]
    return kept + "…"


def share_name(clip: Clip, *, index: int = 1, total: int = 1) -> str:
    """``<date>-<uploader>-<tweet id>.mp4``, disambiguated when a tweet has several clips.

    Sortable by date first, and greppable by author or tweet id — the share has
    no retention policy, so the name is the only index it will ever have.
    """
    uploader = _safe(clip.uploader)
    tweet_id = _safe(clip.tweet_id)
    suffix = f"-{index}" if total > 1 else ""
    extension = clip.path.suffix if _EXTENSION.fullmatch(clip.path.suffix) else ".mp4"
    return f"{clip.upload_date:%Y-%m-%d}-{uploader}-{tweet_id}{suffix}{extension}"


def _safe(value: str) -> str:
    return _UNSAFE_IN_FILENAME.sub("_", value).strip("_") or "unknown"
