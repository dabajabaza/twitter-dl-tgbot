"""Finding tweet links in a message, and unwrapping the short ones.

A *tweet link* is a URL naming a single X post. Two shapes arrive in practice:
the direct one (``x.com/<user>/status/<id>``, plus the twitter.com and mobile.
spellings that still circulate) and the wrapped one (``t.co/<slug>``, which the
X apps produce and which says nothing about its target until followed).
"""

import re

import aiohttp

from twitter_dl.errors import NetworkUnavailable, NotATweetLink

# The username part is deliberately loose (X caps handles at 15 chars, but
# `i/web` and `i/status` are also valid prefixes) while the id is strictly
# numeric — that is the part we key on. A trailing photo/video segment, query
# string or fragment is common in shared links and simply left alone.
_TWEET_URL = re.compile(
    r"https?://(?:www\.|mobile\.|m\.)?(?:twitter|x)\.com/"
    r"(?:i/web/|i/)?[A-Za-z0-9_]{1,20}/status(?:es)?/(\d+)"
    r"[^\s<>\"']*",
    re.IGNORECASE,
)
_SHORT_URL = re.compile(r"https?://t\.co/[A-Za-z0-9]+", re.IGNORECASE)
_TWEET_ID = re.compile(r"/status(?:es)?/(\d+)")

_RESOLVE_TIMEOUT_S = 15
# t.co answers with a redirect for ordinary links, but hands back an HTML
# interstitial for some. Reading a little of the body covers that case; the cap
# keeps a hostile or broken response from being read forever.
_INTERSTITIAL_READ_BYTES = 64 * 1024


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
        for match in (*_TWEET_URL.finditer(source), *_SHORT_URL.finditer(source)):
            url = match.group(0).rstrip(".,;:!?)")
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


async def resolve_short_link(url: str, *, proxy: str | None = None) -> str:
    """Follow a t.co wrapper to the tweet it points at.

    Raises `NotATweetLink` when it points anywhere else — an external article,
    an X profile, a dead slug — because that is a user mistake with a useful
    answer, not a download failure.
    """
    timeout = aiohttp.ClientTimeout(total=_RESOLVE_TIMEOUT_S)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url, proxy=proxy, allow_redirects=True) as response,
        ):
            final = str(response.url)
            if is_tweet_link(final):
                return final
            body = await response.content.read(_INTERSTITIAL_READ_BYTES)
    except aiohttp.ClientError as exc:
        raise NetworkUnavailable(f"could not follow {url}: {exc}") from exc
    except TimeoutError as exc:
        raise NetworkUnavailable(f"timed out following {url}") from exc

    inside = extract_links(body.decode("utf-8", errors="ignore"))
    for candidate in inside:
        if is_tweet_link(candidate):
            return candidate
    raise NotATweetLink(f"{url} does not lead to a tweet")
