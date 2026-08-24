"""The delivery caption: tweet text, an author footer, and Telegram's limit."""

from clipivore.bot.captions import CAPTION_LIMIT, build_caption, utf16_length

TWEET = "https://x.com/cats/status/1"
PROFILE = "https://x.com/cats"
FOOTER = f'<a href="{PROFILE}">@cats</a> · <a href="{TWEET}">Open in X</a>'
# The rendered footer — "@cats · Open in X" — as Telegram will count it.
FOOTER_RENDERED_UNITS = utf16_length("@cats · Open in X")


def caption(description: str) -> str:
    return build_caption(description, TWEET, uploader="cats", uploader_url=PROFILE)


def rendered(built: str) -> str:
    """The text Telegram counts: anchors collapse to their inner text."""
    import re

    return re.sub(r"<a href=\"[^\"]*\">([^<]*)</a>", r"\1", built)


class TestShape:
    def test_a_tweet_without_text_is_just_the_footer(self) -> None:
        assert caption("") == FOOTER

    def test_text_and_footer_are_separated_by_a_blank_line(self) -> None:
        assert caption("A cat video.") == f"A cat video.\n\n{FOOTER}"

    def test_without_an_uploader_the_footer_is_only_the_tweet_link(self) -> None:
        built = build_caption("text", TWEET)
        assert built == f'text\n\n<a href="{TWEET}">Open in X</a>'

    def test_without_a_profile_url_the_handle_stays_plain_text(self) -> None:
        built = build_caption("text", TWEET, uploader="cats")
        assert built == f'text\n\n@cats · <a href="{TWEET}">Open in X</a>'


class TestEscaping:
    def test_html_in_the_tweet_text_cannot_become_markup(self) -> None:
        built = caption('<b>&"bold"</b>')
        assert built.startswith("&lt;b&gt;&amp;&quot;bold&quot;&lt;/b&gt;")

    def test_html_in_the_handle_cannot_become_markup(self) -> None:
        built = build_caption("", TWEET, uploader="a<b>&c", uploader_url=PROFILE)
        assert ">@a&lt;b&gt;&amp;c</a>" in built

    def test_an_ampersand_in_either_url_is_escaped_inside_href(self) -> None:
        built = build_caption(
            "", "https://x.com/a?b=1&c=2", uploader="cats", uploader_url="https://x.com/u?a&b"
        )
        assert 'href="https://x.com/a?b=1&amp;c=2"' in built
        assert 'href="https://x.com/u?a&amp;b"' in built


class TestFitting:
    def test_a_long_text_is_trimmed_so_the_rendered_caption_hits_the_limit(self) -> None:
        built = caption("z" * 5000)
        budget = CAPTION_LIMIT - FOOTER_RENDERED_UNITS - 2
        assert built.startswith("z" * (budget - 1) + "…")
        assert utf16_length(rendered(built)) == CAPTION_LIMIT

    def test_emoji_are_measured_the_way_telegram_measures_them(self) -> None:
        built = caption("😀" * 600)
        assert utf16_length(rendered(built)) <= CAPTION_LIMIT

    def test_trimming_never_splits_a_surrogate_pair(self) -> None:
        built = caption("😀" * 600)
        assert built.encode("utf-16-le").decode("utf-16-le") == built

    def test_trimming_never_cuts_an_entity_in_half(self) -> None:
        # Escaping happens after the trim, so every ampersand the text kept
        # must still be a complete "&amp;".
        built = caption("&" * 2000)
        budget = CAPTION_LIMIT - FOOTER_RENDERED_UNITS - 2
        assert built.startswith("&amp;" * (budget - 1) + "…")

    def test_the_footer_is_never_truncated(self) -> None:
        built = caption("z" * 5000)
        assert built.endswith(f' · <a href="{TWEET}">Open in X</a>')
        assert f'<a href="{PROFILE}">@cats</a>' in built
