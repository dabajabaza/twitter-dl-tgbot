"""Finding tweet links in a message, and unwrapping the short ones.

A *tweet link* is a URL naming a single X post. Two shapes arrive in practice:
the direct one (``x.com/<user>/status/<id>``, plus the twitter.com and mobile.
spellings that still circulate) and the wrapped one (``t.co/<slug>``, which the
X apps produce and which says nothing about its target until followed).
"""

import logging
import re
from urllib.parse import urljoin, urlsplit

import aiohttp

from twitter_dl.errors import NetworkUnavailable, NotATweetLink

logger = logging.getLogger(__name__)

# The username part is deliberately loose (X caps handles at 15 chars, but
# `i/web` and `i/status` are also valid prefixes) while the id is strictly
# numeric — that is the part we key on. A trailing photo/video segment, query
# string or fragment is common in shared links and kept.
#
# The tail excludes commas as well as whitespace: "…/status/111,https://x.com/…"
# is how two links arrive glued together in forwarded text, and a greedy tail
# swallowed the second one into the first, silently losing a request.
_TWEET_PATTERN = (
    r"https?://(?:www\.|mobile\.|m\.)?(?:twitter|x)\.com/"
    r"(?:i/web/|i/)?[A-Za-z0-9_]{1,20}/status(?:es)?/(?P<id>\d+)"
    r"[^\s<>\"',]*"
)
_SHORT_PATTERN = r"https?://t\.co/[A-Za-z0-9]+"
# One pass over the text, so links come back in the order a person wrote them.
# Two passes (all direct, then all short) reordered them, and with a nearly full
# queue that decided which of someone's links got dropped.
_ANY_LINK = re.compile(f"(?:{_TWEET_PATTERN})|(?:{_SHORT_PATTERN})", re.IGNORECASE)
_TWEET_URL = re.compile(_TWEET_PATTERN, re.IGNORECASE)
_SHORT_URL = re.compile(_SHORT_PATTERN, re.IGNORECASE)
_TWEET_ID = re.compile(r"/status(?:es)?/(\d+)")

_RESOLVE_TIMEOUT_S = 15
_MAX_REDIRECTS = 5
# Every hop of a t.co redirect must land on one of these. A shortlink is
# attacker-controlled content — the author of a tweet chooses where it points,
# and a whitelisted user only has to forward the post. Since the only thing
# worth following is a tweet, anything else is refused *before* the request is
# made, which also keeps the bot from being used to reach the LAN it sits on
# (the jail shares the host's network stack, and the router's admin panel is one
# hop away).
_ALLOWED_HOSTS = frozenset(
    {
        "t.co",
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "m.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "m.twitter.com",
    }
)


def extract_links(*sources: str | None) -> list[str]:
    """Return every tweet link and t.co shortlink found across ``sources``.

    Order of first appearance is preserved and duplicates are dropped, so
    forwarding a message that repeats the same link enqueues it once.
    """
    found: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if not source:
            continue
        for match in _ANY_LINK.finditer(source):
            url = match.group(0).rstrip(".;:!?)")
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def is_short_link(url: str) -> bool:
    """True for a t.co wrapper, whose target is unknown until it is followed."""
    return bool(_SHORT_URL.fullmatch(url))


def is_tweet_link(url: str) -> bool:
    """True for a URL that names a single post directly."""
    return bool(_TWEET_URL.match(url))


def tweet_id(url: str) -> str | None:
    """The numeric post id, which is what X and file names actually key on."""
    match = _TWEET_ID.search(url)
    return match.group(1) if match else None


def _host_allowed(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in _ALLOWED_HOSTS


async def resolve_short_link(url: str, *, proxy: str | None = None) -> str:
    """Follow a t.co wrapper to the tweet it points at.

    Redirects are followed by hand rather than by aiohttp, so each hop's host is
    checked *before* it is requested. Raises `NotATweetLink` when the chain
    leaves X — an external article, a profile, a dead slug — because that is a
    user mistake with a useful answer, not a download failure.
    """
    timeout = aiohttp.ClientTimeout(total=_RESOLVE_TIMEOUT_S)
    current = url
    body = b""
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for _ in range(_MAX_REDIRECTS):
                if not _host_allowed(current):
                    raise NotATweetLink(f"{url} leads outside X")
                async with session.get(current, proxy=proxy, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            break
                        current = urljoin(current, location)
                        if is_tweet_link(current):
                            return current
                        continue
                    if is_tweet_link(str(response.url)):
                        return str(response.url)
                    # t.co sometimes answers with an HTML interstitial instead
                    # of a redirect; the target is in the body.
                    body = await response.content.read(64 * 1024)
                    break
            else:
                raise NotATweetLink(f"{url} redirects too many times")
    except aiohttp.ClientError as exc:
        raise NetworkUnavailable(f"could not follow {url}: {exc}") from exc
    except TimeoutError as exc:
        raise NetworkUnavailable(f"timed out following {url}") from exc

    for candidate in extract_links(body.decode("utf-8", errors="ignore")):
        if is_tweet_link(candidate):
            return candidate
    raise NotATweetLink(f"{url} does not lead to a tweet")
