"""Every string the bot says, in one place.

English throughout, and plain text rather than HTML or Markdown: replies quote
things the bot does not control — uploader handles, yt-dlp messages, external
locators full of punctuation — and plain text is the only format none of them
can break. The one exception is the delivery verdict, whose HTML lives in
bot/captions.py with every foreign string escaped; this module stays plain.
"""

from clipivore.services.overflow import (
    SAVED_SELECTION_ID,
    OverflowCatalog,
    OverflowChoice,
    OverflowState,
)

HELP = (
    "Send me a link to an X post and I'll download the video from it.\n\n"
    "Links look like https://x.com/i/status/0 — t.co short links work too, "
    "and a message may hold several links at once.\n\n"
    "Clips up to {max_mb} MB arrive here in the chat. {overflow}"
)

HELP_OVERFLOW_READY = "Larger clips are delivered through {adapter}."
HELP_OVERFLOW_OFF = "Larger clips cannot be delivered while Overflow delivery is off."
HELP_OVERFLOW_MISSING = (
    "Larger clips cannot be delivered because the selected Overflow Adapter is missing."
)
HELP_OVERFLOW_MISCONFIGURED = (
    "Larger clips cannot be delivered because Overflow delivery is configured incorrectly."
)

NO_LINK = "No tweet link found. Send me a link to an X post."
NOT_A_TWEET = "That short link doesn't lead to a tweet."

QUEUED = "Queued…"
QUEUED_POSITION = "Queued — {position} in line."
QUEUE_FULL = "Queue is full ({limit} requests). Try again in a few minutes."
DOWNLOADING = "Downloading…"
DOWNLOADING_PROGRESS = "Downloading… {progress}"
# X clips usually arrive as separate video and audio files, each reporting its
# own 0→100%; naming the stream keeps the second run from looking like a restart.
DOWNLOADING_VIDEO_PROGRESS = "Downloading video… {progress}"
DOWNLOADING_AUDIO_PROGRESS = "Downloading audio… {progress}"
UPLOADING = "Uploading to Telegram… ({size})"
UPLOADING_MANY = "Uploading to Telegram… ({index}/{total}, {size})"
DELIVERING_OVERFLOW = "Too big for Telegram — delivering through {adapter}…"
SENT = "Sent."
# The label bot/captions.py wraps into the link to the tweet itself.
OPEN_IN_X = "Open in X"

OVERFLOW_RESULT = "Too big for Telegram ({size}). Delivered through {adapter}:\n{location}"

NO_VIDEO = "That tweet has no video in it."
TWEET_UNAVAILABLE = "Can't reach that tweet — it may be deleted, protected or suspended."
NETWORK_UNAVAILABLE = "Can't reach X right now (network or proxy is down). Try again later."
DOWNLOAD_FAILED = "Download failed. The details are in the bot's log."
OVERFLOW_FAILED = "Downloaded it, but {adapter} couldn't complete Overflow delivery."
OVERFLOW_DISABLED = (
    "This clip is larger than Telegram's {max_mb} MB limit{observed}. Overflow delivery is off."
)
OVERFLOW_MISSING = (
    "This clip is larger than Telegram's {max_mb} MB limit{observed}. The selected Overflow "
    "Adapter ({adapter}) is missing. The owner needs to choose another in Menu."
)
OVERFLOW_MISCONFIGURED = (
    "This clip is larger than Telegram's {max_mb} MB limit{observed}. Overflow delivery through "
    "{adapter} is configured incorrectly. The owner needs to choose another in Menu."
)
# How the size in the verdict was learned: an aborted download versus a clip
# that finished downloading and only then failed the ceiling.
OVERFLOW_STOPPED_AT = "stopped at {size}"
OVERFLOW_CLIP_SIZE = "the clip is {size}"

OVERFLOW_MENU = "Overflow delivery\n\nCurrent: {current}"
OVERFLOW_MENU_PROBLEMS = "Unavailable:\n{problems}"
OVERFLOW_COMMAND_DESCRIPTION = "Overflow delivery"
OVERFLOW_CURRENT_MARKER = "✓ "
OVERFLOW_OFF_LABEL = "Off"
OVERFLOW_SAVED_SELECTION = "Saved selection"
OVERFLOW_STATE_MISSING = "missing"
OVERFLOW_STATE_MISCONFIGURED = "misconfigured"
OVERFLOW_NOT_SELECTABLE = "That Overflow Adapter is not available."
OVERFLOW_SAVE_FAILED = "Couldn't save the Overflow delivery selection. See the bot's log."
OVERFLOW_SELECTED = "Selected {adapter}."
TIMED_OUT = "Gave up after {minutes} minutes — the video is too long or the link is too slow."
AUTH_EXPIRED = "Can't download right now: the owner's X session needs renewing. Owner notified."

OWNER_AUTH_EXPIRED = (
    "X rejected the stored cookies — export cookies.txt from the browser again and "
    "replace {path} on the server.\n\nX said: {detail}"
)

FFMPEG_MISSING = (
    "ffmpeg is not installed, so only single-stream formats can be downloaded and "
    "quality will be capped below what the account can see."
)


def help_message(max_mb: int, overflow: OverflowChoice) -> str:
    if overflow.state is OverflowState.READY:
        detail = HELP_OVERFLOW_READY.format(adapter=overflow.label)
    elif overflow.state is OverflowState.MISSING:
        detail = HELP_OVERFLOW_MISSING
    elif overflow.state is OverflowState.MISCONFIGURED:
        detail = HELP_OVERFLOW_MISCONFIGURED
    else:
        detail = HELP_OVERFLOW_OFF
    return HELP.format(max_mb=max_mb, overflow=detail)


def downloading_progress(stream: str, progress: str) -> str:
    if stream == "video":
        return DOWNLOADING_VIDEO_PROGRESS.format(progress=progress)
    if stream == "audio":
        return DOWNLOADING_AUDIO_PROGRESS.format(progress=progress)
    return DOWNLOADING_PROGRESS.format(progress=progress)


def overflow_unavailable(overflow: OverflowChoice, *, max_mb: int, observed: str = "") -> str:
    """The size-limit verdict; ``observed`` says how far the download got."""
    detail = f" ({observed})" if observed else ""
    if overflow.state is OverflowState.MISSING:
        return OVERFLOW_MISSING.format(max_mb=max_mb, adapter=overflow.label, observed=detail)
    if overflow.state is OverflowState.MISCONFIGURED:
        return OVERFLOW_MISCONFIGURED.format(
            max_mb=max_mb, adapter=overflow_label(overflow), observed=detail
        )
    return OVERFLOW_DISABLED.format(max_mb=max_mb, observed=detail)


def overflow_menu(catalog: OverflowCatalog) -> str:
    current = catalog.current
    current_text = overflow_label(current)
    if current.state in {OverflowState.MISSING, OverflowState.MISCONFIGURED}:
        current_text += f" — {overflow_state_label(current.state)}"
    text = OVERFLOW_MENU.format(current=current_text)
    problems = [
        f"⚠ {choice.label} — {overflow_state_label(choice.state)}"
        for choice in catalog.choices
        if choice.state in {OverflowState.MISSING, OverflowState.MISCONFIGURED}
    ]
    if problems:
        text += "\n\n" + OVERFLOW_MENU_PROBLEMS.format(problems="\n".join(problems))
    return text


def overflow_label(overflow: OverflowChoice) -> str:
    if overflow.adapter_id == SAVED_SELECTION_ID:
        return OVERFLOW_SAVED_SELECTION
    if overflow.state is OverflowState.OFF:
        return OVERFLOW_OFF_LABEL
    return overflow.label


def overflow_state_label(state: OverflowState) -> str:
    if state is OverflowState.MISSING:
        return OVERFLOW_STATE_MISSING
    return OVERFLOW_STATE_MISCONFIGURED


def human_size(size_bytes: int) -> str:
    """Size as a person would say it, which is all these numbers are used for."""
    kilobytes = size_bytes / 1024
    megabytes = kilobytes / 1024
    if megabytes >= 1024:
        return f"{megabytes / 1024:.1f} GB"
    if megabytes >= 1:
        return f"{megabytes:.0f} MB"
    # A short GIF-mp4 is well under a megabyte; "0 MB" would read as a glitch.
    return f"{kilobytes:.0f} KB"
