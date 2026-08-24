"""Bluesky as a Provider: the links it claims, and the ones it must not.

The URL fixtures are the ones yt-dlp's own extractor is tested against, so a
disagreement between this bot's patterns and the engine's shows up here rather
than on a live download.
"""

import pytest

from clipivore.providers import bluesky
from clipivore.providers.bluesky import BlueskyProvider
from clipivore.providers.x import XProvider
from clipivore.services.providers import ProviderCatalog, ProviderContext

CATALOG = ProviderCatalog(ProviderContext())

HANDLE_POST = "https://bsky.app/profile/blu3blue.bsky.social/post/3l4omssdl632g"
DID_POST = "https://bsky.app/profile/did:plc:z72i7hdynmk6r22z27h6tvur/post/3l3vgf77uco2g"


def urls(*sources: str | None) -> list[str]:
    return [link.url for link in CATALOG.extract(*sources)]


@pytest.mark.parametrize(
    "url",
    [
        HANDLE_POST,
        DID_POST,
        "https://www.bsky.app/profile/bsky.app/post/3l3vgf77uco2g",
        "http://bsky.app/profile/souris.moe/post/3l4qhp7bcs52c",
        "https://bsky.app/profile/blu3blue.bsky.social/post/3l4omssdl632g?foo=bar",
    ],
)
def test_every_spelling_of_a_bluesky_post_link_is_recognised(url: str) -> None:
    assert urls(url) == [url]
    assert BlueskyProvider.post_link.match(url)


@pytest.mark.parametrize(
    "text",
    [
        "https://bsky.app/profile/blu3blue.bsky.social",  # a profile, not a post
        "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.post/3l3vgf77uco2g",
        "https://main.bsky.dev/profile/souris.moe/post/3l4qhp7bcs52c",
        "https://bsky.app/profile/someone/post/",
    ],
)
def test_spellings_deliberately_left_alone_are_not_claimed(text: str) -> None:
    # at:// is not a link people share, and main.bsky.dev is staging. Both are
    # accepted by yt-dlp and refused here on purpose.
    assert urls(text) == []


def test_the_rkey_is_what_the_post_is_keyed_on() -> None:
    assert BlueskyProvider.post_id(HANDLE_POST) == "3l4omssdl632g"
    assert BlueskyProvider.post_id(DID_POST) == "3l3vgf77uco2g"


def test_a_bluesky_link_has_no_short_form_and_never_touches_the_network() -> None:
    assert BlueskyProvider.short_link is None
    assert not BlueskyProvider.is_short(HANDLE_POST)


async def test_a_direct_link_resolves_to_itself() -> None:
    provider = BlueskyProvider(ProviderContext())
    assert await provider.resolve(HANDLE_POST) == HANDLE_POST


def test_two_links_glued_by_a_comma_are_two_links() -> None:
    second = "https://bsky.app/profile/bsky.app/post/3l3vgf77uco2g"
    assert urls(f"{HANDLE_POST},{second}") == [HANDLE_POST, second]


class TestTheTwoProvidersDoNotOverlap:
    def test_each_platform_claims_only_its_own_links(self) -> None:
        assert CATALOG.claim(HANDLE_POST) is not None
        assert CATALOG.claim(HANDLE_POST).name == "Bluesky"  # type: ignore[union-attr]
        assert CATALOG.claim("https://x.com/a/status/1").name == "X"  # type: ignore[union-attr]
        assert not XProvider.post_link.match(HANDLE_POST)
        assert not BlueskyProvider.post_link.match("https://x.com/a/status/1")

    def test_links_to_both_platforms_keep_the_order_they_were_written(self) -> None:
        # The reason the catalog builds one combined pattern instead of asking
        # each Provider in turn: a per-Provider pass groups by platform, and
        # with a nearly full queue that decides whose link gets dropped.
        tweet = "https://x.com/someone/status/1234567890"
        assert urls(f"{HANDLE_POST} then {tweet}") == [HANDLE_POST, tweet]
        assert urls(f"{tweet} then {HANDLE_POST}") == [tweet, HANDLE_POST]


class TestBlueskyNeedsNoConfiguration:
    def test_it_holds_no_cookie_session_so_it_can_never_alert_the_owner(self) -> None:
        # An Auth expiry alert says "the Owner must re-export cookies". For a
        # Provider with no session that would be advice about a file that does
        # not exist.
        assert BlueskyProvider(ProviderContext()).cookies is None
        assert bluesky.PROFILE.auth_markers == ()

    def test_it_is_ready_with_an_empty_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("COOKIES_FILE", "YTDLP_PROXY", "TELEGRAM_PROXY"):
            monkeypatch.delenv(name, raising=False)
        choice = ProviderCatalog(ProviderContext()).get("bluesky")
        assert choice is not None
        assert choice.ready
