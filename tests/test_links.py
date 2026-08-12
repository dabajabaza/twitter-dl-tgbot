import pytest

from twitter_dl.errors import NotATweetLink
from twitter_dl.services.links import (
    extract_links,
    is_short_link,
    is_tweet_link,
    resolve_short_link,
    tweet_id,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/someone/status/1234567890",
        "https://twitter.com/someone/status/1234567890",
        "https://www.twitter.com/someone/status/1234567890",
        "https://mobile.twitter.com/someone/status/1234567890",
        "http://x.com/someone/statuses/1234567890",
        "https://x.com/someone/status/1234567890?s=20&t=abc",
        "https://x.com/someone/status/1234567890/video/1",
        "https://x.com/i/web/status/1234567890",
    ],
)
def test_every_spelling_of_a_tweet_link_in_the_wild_is_recognised(url: str) -> None:
    assert extract_links(url) == [url]
    assert is_tweet_link(url)


@pytest.mark.parametrize(
    "text",
    [
        "no links here at all",
        "https://x.com/someone",  # a profile, not a post
        "https://youtube.com/watch?v=abc",
        "https://example.com/someone/status/123",
    ],
)
def test_things_that_are_not_tweet_links_are_left_alone(text: str) -> None:
    assert extract_links(text) == []


def test_a_link_is_found_inside_ordinary_prose() -> None:
    text = "look at this https://x.com/someone/status/1234567890 it's great"
    assert extract_links(text) == ["https://x.com/someone/status/1234567890"]


def test_a_sentence_ending_punctuation_mark_is_not_part_of_the_link() -> None:
    text = "see https://x.com/someone/status/1234567890."
    assert extract_links(text) == ["https://x.com/someone/status/1234567890"]


def test_several_links_keep_their_order_and_repeats_collapse() -> None:
    first = "https://x.com/a/status/1"
    second = "https://x.com/b/status/2"
    assert extract_links(f"{first} {second} {first}") == [first, second]


def test_links_are_gathered_from_every_source_offered() -> None:
    caption = "https://x.com/a/status/1"
    hidden = "https://x.com/b/status/2"
    assert extract_links(None, caption, hidden) == [caption, hidden]


def test_short_links_are_accepted_but_marked_as_unresolved() -> None:
    short = "https://t.co/AbC123"
    assert extract_links(short) == [short]
    assert is_short_link(short)
    assert not is_tweet_link(short)


def test_a_direct_link_is_never_mistaken_for_a_short_one() -> None:
    assert not is_short_link("https://x.com/someone/status/1234567890")


def test_the_post_id_is_what_the_link_is_keyed_on() -> None:
    assert tweet_id("https://x.com/someone/status/1234567890?s=20") == "1234567890"
    assert tweet_id("https://t.co/AbC123") is None


class TestLinksThatArriveGluedOrOutOfOrder:
    def test_two_links_joined_by_a_comma_are_two_links(self) -> None:
        # How they arrive in forwarded text. A greedy tail swallowed the second
        # one into the first, and that request was silently never made.
        first = "https://x.com/a/status/1234567890"
        second = "https://x.com/b/status/9876543210"
        assert extract_links(f"{first},{second}") == [first, second]

    def test_links_come_back_in_the_order_they_were_written(self) -> None:
        # Scanning direct links first and short ones second reordered them, and
        # with a nearly full queue that decided whose links got dropped.
        short = "https://t.co/AbC123"
        direct = "https://x.com/b/status/111"
        assert extract_links(f"{short} then {direct}") == [short, direct]

    def test_a_query_string_still_survives_intact(self) -> None:
        url = "https://x.com/a/status/1234567890?s=20&t=abc"
        assert extract_links(url) == [url]


class TestShortLinkResolution:
    """t.co points wherever the tweet's author decided — including inward."""

    async def test_a_redirect_off_x_is_refused_before_it_is_requested(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import aiohttp

        requested: list[str] = []

        class Boom:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "Boom":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            def get(self, url: str, **kwargs: object) -> object:
                requested.append(url)
                raise AssertionError(f"must not request {url}")

        monkeypatch.setattr(aiohttp, "ClientSession", Boom)

        # The bot sits on the home LAN with the router's admin panel one hop
        # away; a forwarded tweet must not be able to make it knock there.
        with pytest.raises(NotATweetLink):
            await resolve_short_link("https://evil.example/redirect")
        assert requested == []

    async def test_a_shortlink_leading_to_a_tweet_resolves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = "https://x.com/someone/status/1234567890"
        _install_fake_session(monkeypatch, status=301, location=target)

        assert await resolve_short_link("https://t.co/AbC123") == target

    async def test_a_shortlink_leading_anywhere_else_is_a_user_mistake(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_session(monkeypatch, status=301, location="https://example.com/article")

        with pytest.raises(NotATweetLink):
            await resolve_short_link("https://t.co/AbC123")


def _install_fake_session(monkeypatch: pytest.MonkeyPatch, *, status: int, location: str) -> None:
    import aiohttp

    class FakeResponse:
        def __init__(self) -> None:
            self.status = status
            self.headers = {"Location": location}
            self.url = location

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def get(self, url: str, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
