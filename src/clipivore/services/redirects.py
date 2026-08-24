"""Following a short link by hand, checking every hop before it is requested.

A short link is attacker-controlled content: the author of a post chooses where
it points, and an allowed user only has to forward the post. So redirects are
not handed to aiohttp — each hop's host is checked against the caller's
allowlist *first*, which is also what keeps the bot from being used to reach the
LAN it sits on (the jail shares the host's network stack, and the router's admin
panel is one hop away).

The mechanics are shared; what counts as an acceptable destination is not. A
Provider supplies its own allowlist and its own idea of a post link.
"""

import logging
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import aiohttp

from clipivore.errors import NetworkUnavailable, NotAPostLink

logger = logging.getLogger(__name__)

RESOLVE_TIMEOUT_S = 15
MAX_REDIRECTS = 5
# t.co and its kin sometimes answer with an HTML interstitial instead of a
# redirect; the target is in the body, and this much of it is plenty.
_INTERSTITIAL_BYTES = 64 * 1024
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def host_allowed(url: str, allowed_hosts: frozenset[str]) -> bool:
    return (urlsplit(url).hostname or "").lower() in allowed_hosts


async def follow(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    is_target: Callable[[str], bool],
    find_targets: Callable[[str], Iterable[str]],
    proxy: str | None = None,
    outside_message: str,
) -> str:
    """Follow ``url`` until it lands on something ``is_target`` accepts.

    Raises `NotAPostLink` when the chain leaves the allowlist, runs too long, or
    ends somewhere that is not a post — those are user mistakes with a useful
    answer, not download failures. Network trouble raises `NetworkUnavailable`.
    """
    timeout = aiohttp.ClientTimeout(total=RESOLVE_TIMEOUT_S)
    current = url
    body = b""
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for _ in range(MAX_REDIRECTS):
                if not host_allowed(current, allowed_hosts):
                    raise NotAPostLink(outside_message)
                async with session.get(current, proxy=proxy, allow_redirects=False) as response:
                    if response.status in _REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            break
                        current = urljoin(current, location)
                        if is_target(current):
                            return current
                        continue
                    if is_target(str(response.url)):
                        return str(response.url)
                    body = await response.content.read(_INTERSTITIAL_BYTES)
                    break
            else:
                raise NotAPostLink(f"{url} redirects too many times")
    except aiohttp.ClientError as exc:
        raise NetworkUnavailable(f"could not follow {url}: {exc}") from exc
    except TimeoutError as exc:
        raise NetworkUnavailable(f"timed out following {url}") from exc

    for candidate in find_targets(body.decode("utf-8", errors="ignore")):
        if is_target(candidate):
            return candidate
    raise NotAPostLink(f"{url} does not lead to a post")
