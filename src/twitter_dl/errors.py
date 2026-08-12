"""Failure taxonomy.

yt-dlp reports everything as one ``DownloadError`` carrying a human sentence, so
the difference between "my cookies died", "this tweet has no video" and "the
proxy is down" only exists if someone draws it. The bot draws it here: the
worker picks a reply per class, and exactly one class (`AuthExpired`) is worth
waking the owner for.
"""


class TwitterDlError(Exception):
    """Anything this bot knows how to explain to a human."""


class NotATweetLink(TwitterDlError):
    """A t.co shortlink that turned out to point somewhere other than a tweet."""


class NoVideoInTweet(TwitterDlError):
    """The tweet exists and is readable, it just has no video in it."""


class TweetUnavailable(TwitterDlError):
    """Deleted, suspended, protected, or otherwise not there for us."""


class AuthExpired(TwitterDlError):
    """The cookie session no longer authenticates.

    The one failure the owner must act on: everything keeps "working" for public
    tweets while NSFW, age-gated and protected content silently stops.
    """


class NetworkUnavailable(TwitterDlError):
    """The proxy or the network is down — nothing to fix in the bot itself."""


class DownloadFailed(TwitterDlError):
    """yt-dlp failed in a way this taxonomy does not recognise."""


class ShareUnavailable(TwitterDlError):
    """The clip was downloaded but rclone could not put it on the share."""
