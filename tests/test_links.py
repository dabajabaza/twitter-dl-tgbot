import pytest

from twitter_dl.services.links import (
    extract_links,
    is_short_link,
    is_tweet_link,
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
