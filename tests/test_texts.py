"""The bot's voice: English, plain text, and no placeholder left unfilled."""

import re

from twitter_dl.bot import texts

_PUBLIC = {
    name: value for name, value in vars(texts).items() if name.isupper() and isinstance(value, str)
}
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_MARKUP = re.compile(r"</?[a-z]+>|\*\*|__")


def test_there_is_something_to_say_in_every_situation() -> None:
    assert _PUBLIC
    assert all(value.strip() for value in _PUBLIC.values())


def test_the_bot_speaks_english() -> None:
    russian = {name for name, value in _PUBLIC.items() if _CYRILLIC.search(value)}
    assert not russian


def test_nothing_is_formatted_as_html_or_markdown() -> None:
    # Replies quote uploader handles, yt-dlp messages and Windows share paths;
    # plain text is the only format none of them can break.
    marked_up = {name for name, value in _PUBLIC.items() if _MARKUP.search(value)}
    assert not marked_up


def test_every_placeholder_is_one_the_caller_actually_supplies() -> None:
    supplied = {
        "HELP": {"max_mb"},
        "QUEUE_FULL": {"limit"},
        "QUEUED_POSITION": {"position"},
        "DOWNLOADING_PROGRESS": {"progress"},
        "UPLOADING_MANY": {"index", "total"},
        "SHARE_RESULT": {"size", "path"},
        "TIMED_OUT": {"minutes"},
        "OWNER_AUTH_EXPIRED": {"path", "detail"},
    }
    for name, value in _PUBLIC.items():
        placeholders = set(re.findall(r"\{(\w+)\}", value))
        assert placeholders == supplied.get(name, set()), name


class TestHumanSize:
    def test_megabytes_are_whole_numbers_because_nobody_reads_the_decimals(self) -> None:
        assert texts.human_size(52 * 1024 * 1024) == "52 MB"

    def test_a_gigabyte_scale_file_is_not_reported_as_four_digits_of_megabytes(self) -> None:
        assert texts.human_size(2 * 1024 * 1024 * 1024) == "2.0 GB"
