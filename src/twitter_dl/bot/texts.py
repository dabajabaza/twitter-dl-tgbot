"""Every string the bot says, in one place.

English throughout, and plain text rather than HTML or Markdown: replies quote
things the bot does not control — uploader handles, yt-dlp messages, Windows
share paths full of backslashes — and plain text is the only format none of
them can break.
"""

HELP = (
    "Send me a link to an X post and I'll download the video from it.\n\n"
    "Links look like https://x.com/user/status/1234567890 — t.co short links work too, "
    "and a message may hold several links at once.\n\n"
    "Clips up to {max_mb} MB arrive here in the chat. Anything larger goes to the home "
    "share instead, and I'll reply with the path."
)

NO_LINK = "No tweet link found. Send me a link to an X post."
NOT_A_TWEET = "That short link doesn't lead to a tweet."

QUEUED = "Queued…"
QUEUED_POSITION = "Queued — {position} in line."
QUEUE_FULL = "Queue is full ({limit} requests). Try again in a few minutes."
DOWNLOADING = "Downloading…"
DOWNLOADING_PROGRESS = "Downloading… {progress}"
UPLOADING = "Uploading to Telegram…"
UPLOADING_MANY = "Uploading to Telegram… ({index}/{total})"
COPYING_TO_SHARE = "Too big for Telegram — copying to the share…"
SENT = "Sent."

SHARE_RESULT = "Too big for Telegram ({size}). Saved to the share:\n{path}"

NO_VIDEO = "That tweet has no video in it."
TWEET_UNAVAILABLE = "Can't reach that tweet — it may be deleted, protected or suspended."
NETWORK_UNAVAILABLE = "Can't reach X right now (network or proxy is down). Try again later."
DOWNLOAD_FAILED = "Download failed. The details are in the bot's log."
SHARE_FAILED = "Downloaded it, but couldn't write to the share."
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


def human_size(size_bytes: int) -> str:
    """Size as a person would say it, which is all these numbers are used for."""
    megabytes = size_bytes / 1024 / 1024
    if megabytes >= 1024:
        return f"{megabytes / 1024:.1f} GB"
    return f"{megabytes:.0f} MB"
