"""Route a downloaded Clip to Telegram or its selected Overflow destination."""

import logging
import re
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import FSInputFile

from twitter_dl.domain import Clip
from twitter_dl.errors import OverflowFailed, OverflowUnavailable
from twitter_dl.services.overflow import OverflowChoice

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
class OverflowDelivery:
    """The clip is outside Telegram, at this human-facing locator."""

    size_bytes: int
    adapter_label: str
    location: str


DeliveryResult = ChatDelivery | OverflowDelivery


class ClipDelivery:
    """Routes each clip by size; an Adapter owns every external destination."""

    def __init__(
        self,
        bot: Bot,
        *,
        max_chat_bytes: int,
    ) -> None:
        self._bot = bot
        self._max_chat_bytes = max_chat_bytes

    async def deliver(
        self,
        clip: Clip,
        *,
        chat_id: int,
        caption: str,
        overflow: OverflowChoice,
        index: int = 1,
        total: int = 1,
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

        if not overflow.ready or overflow.destination is None:
            raise OverflowUnavailable(
                adapter_id=overflow.adapter_id,
                state=overflow.state.value,
            )
        name = overflow_name(clip, index=index, total=total)
        try:
            raw_location = await overflow.destination.store(clip.path, name=name)
            if not isinstance(raw_location, str):
                raise ValueError("OverflowDestination.store returned a non-string locator")
            location = str(raw_location).strip()
            if not location:
                raise ValueError("OverflowDestination.store returned an empty locator")
        except (Exception, SystemExit) as exc:
            logger.exception("overflow adapter %s failed", overflow.adapter_id)
            raise OverflowFailed(adapter_id=overflow.adapter_id, detail=str(exc)) from exc
        return OverflowDelivery(
            size_bytes=size,
            adapter_label=overflow.label,
            location=location,
        )


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


def overflow_name(clip: Clip, *, index: int = 1, total: int = 1) -> str:
    """``<date>-<uploader>-<tweet id>.mp4``, disambiguated for several clips.

    Sortable by date first and greppable by author or tweet id, independent of
    the selected external destination.
    """
    uploader = _safe(clip.uploader)
    tweet_id = _safe(clip.tweet_id)
    suffix = f"-{index}" if total > 1 else ""
    extension = clip.path.suffix if _EXTENSION.fullmatch(clip.path.suffix) else ".mp4"
    return f"{clip.upload_date:%Y-%m-%d}-{uploader}-{tweet_id}{suffix}{extension}"


def _safe(value: str) -> str:
    return _UNSAFE_IN_FILENAME.sub("_", value).strip("_") or "unknown"
