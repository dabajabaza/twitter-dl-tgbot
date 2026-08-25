"""Bluesky as a Provider: the links it claims, and the ones it must not.

The URL fixtures are the ones yt-dlp's own extractor is tested against, so a
disagreement between this bot's patterns and the engine's shows up here rather
than on a live download.
"""

import pytest
from yt_dlp.utils import DownloadError

from clipivore.errors import DownloadFailed, NetworkUnavailable, NoVideoInPost
from clipivore.providers import bluesky
from clipivore.providers.bluesky import BlueskyProvider
from clipivore.providers.twitter import TwitterProvider
from clipivore.services.downloader import _classify
from clipivore.services.providers import ProviderCatalog, ProviderContext

HANDLE_POST = "https://bsky.app/profile/blu3blue.bsky.social/post/3l4omssdl632g"
DID_POST = "https://bsky.app/profile/did:plc:z72i7hdynmk6r22z27h6tvur/post/3l3vgf77uco2g"


def catalog() -> ProviderCatalog:
    """Built per call, so the hermetic-settings fixture has already run.

    A module-level catalog is constructed at collection time, before any
    fixture — which is exactly the environment leak the suite is guarded
    against, and it showed as "provider x is misconfigured" in the collection
    log of anyone who had COOKIES_FILE set.
    """
    return ProviderCatalog(ProviderContext())


def urls(*sources: str | None) -> list[str]:
    return [link.url for link in catalog().extract(*sources)]


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
        assert catalog().claim(HANDLE_POST) is not None
        assert catalog().claim(HANDLE_POST).name == "Bluesky"  # type: ignore[union-attr]
        assert catalog().claim("https://x.com/a/status/1").name == "Twitter"  # type: ignore[union-attr]
        assert not TwitterProvider.post_link.match(HANDLE_POST)
        assert not BlueskyProvider.post_link.match("https://x.com/a/status/1")

    def test_links_to_both_platforms_keep_the_order_they_were_written(self) -> None:
        # The reason the catalog merges every Provider's matches by position
        # instead of taking one Provider at a time: a per-Provider pass groups
        # links by platform, and with a nearly full queue that decides whose
        # link gets dropped.
        tweet = "https://x.com/someone/status/1234567890"
        assert urls(f"{HANDLE_POST} then {tweet}") == [HANDLE_POST, tweet]
        assert urls(f"{tweet} then {HANDLE_POST}") == [tweet, HANDLE_POST]


class TestWhatBlueskysErrorsCanAndCannotSay:
    """The honest half of D19: what the engine's sentences actually carry."""

    def test_a_post_with_no_video_is_named_as_such(self) -> None:
        error = DownloadError("ERROR: [Bluesky] abc: No video could be found in this post")
        assert isinstance(_classify(error, bluesky.PROFILE), NoVideoInPost)

    @pytest.mark.parametrize(
        "message",
        [
            # A deleted or blocked post: XRPC answers 400 with the reason only
            # in the body, which yt-dlp drops.
            "ERROR: [Bluesky] abc: Unable to download JSON metadata: HTTP Error 400: Bad Request",
            # notFoundPost / blockedPost reach the extractor as a bare KeyError.
            "ERROR: [Bluesky] abc: An extractor error has occurred. (caused by KeyError('post'))",
        ],
    )
    def test_a_gone_post_is_only_a_download_failure_and_the_profile_admits_it(
        self, message: str
    ) -> None:
        # Pinned deliberately: this is the limitation D19 records, not a state
        # somebody should "fix" by adding markers that cannot match.
        assert isinstance(_classify(DownloadError(message), bluesky.PROFILE), DownloadFailed)
        assert bluesky.PROFILE.account_state_markers == ()

    def test_an_api_hiccup_is_not_reported_as_a_deleted_post(self) -> None:
        error = DownloadError("ERROR: [Bluesky] abc: HTTP Error 503: Service Unavailable")
        assert isinstance(_classify(error, bluesky.PROFILE), NetworkUnavailable)


class TestAQuotePostIsNamedAfterTheLinkThatWasSent:
    def test_the_id_comes_from_the_url_not_from_the_quoted_post(self) -> None:
        # yt-dlp's own fixture for this URL reports id 3l3vgf77uco2g — the post
        # being quoted — so without the hook the stored file would be named
        # after a post the sender never linked to (D7).
        sent = "https://bsky.app/profile/dannybhoix.bsky.social/post/3l6oe5mtr2c2j"
        assert bluesky.PROFILE.post_id_from_url(sent) == "3l6oe5mtr2c2j"

    def test_a_url_it_does_not_recognise_yields_nothing_rather_than_a_guess(self) -> None:
        assert bluesky.PROFILE.post_id_from_url("https://example.com/x") is None


class TestBlueskyNeedsNoConfiguration:
    def test_it_holds_no_cookie_session_so_it_can_never_alert_the_owner(self) -> None:
        # An Auth expiry alert says "the Owner must re-export cookies". For a
        # Provider with no session that would be advice about a file that does
        # not exist.
        assert BlueskyProvider(ProviderContext()).cookies is None
        assert bluesky.PROFILE.auth_markers == ()

    def test_it_is_ready_with_nothing_configured_at_all(self) -> None:
        # Not just "no cookies": Bluesky reads no environment whatsoever, which
        # is what makes it deployable without touching the server's env file.
        # The hermetic fixture has already emptied everything a Provider reads.
        choice = ProviderCatalog(ProviderContext()).get("bluesky")
        assert choice is not None
        assert choice.ready
