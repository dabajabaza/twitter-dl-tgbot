"""The bot's voice: English, plain text, and no placeholder left unfilled."""

import re

import pytest

from clipivore.bot import texts
from clipivore.services.overflow import OverflowChoice, OverflowState

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
    # Replies quote uploader handles, yt-dlp messages and external locators;
    # plain text is the only format none of them can break.
    marked_up = {name for name, value in _PUBLIC.items() if _MARKUP.search(value)}
    assert not marked_up


def test_every_placeholder_is_one_the_caller_actually_supplies() -> None:
    supplied = {
        "HELP": {"max_mb", "overflow"},
        "HELP_OVERFLOW_READY": {"adapter"},
        "QUEUE_FULL": {"limit"},
        "QUEUED_POSITION": {"position"},
        "DOWNLOADING_PROGRESS": {"progress"},
        "DOWNLOADING_VIDEO_PROGRESS": {"progress"},
        "DOWNLOADING_AUDIO_PROGRESS": {"progress"},
        "UPLOADING": {"size"},
        "UPLOADING_MANY": {"index", "total", "size"},
        "DELIVERING_OVERFLOW": {"adapter"},
        "OVERFLOW_RESULT": {"size", "adapter", "location"},
        "OVERFLOW_FAILED": {"adapter"},
        "OVERFLOW_DISABLED": {"max_mb", "observed"},
        "OVERFLOW_MISSING": {"max_mb", "adapter", "observed"},
        "OVERFLOW_MISCONFIGURED": {"max_mb", "adapter", "observed"},
        "OVERFLOW_STOPPED_AT": {"size"},
        "OVERFLOW_CLIP_SIZE": {"size"},
        "OVERFLOW_MENU": {"current"},
        "OVERFLOW_MENU_PROBLEMS": {"problems"},
        "OVERFLOW_SELECTED": {"adapter"},
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

    def test_a_sub_megabyte_file_is_kilobytes_not_a_zero(self) -> None:
        # A short GIF-mp4 really is this small; "0 MB" reads as a glitch.
        assert texts.human_size(200 * 1024) == "200 KB"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (OverflowState.OFF, "Overflow delivery is off"),
        (OverflowState.MISSING, "is missing"),
        (OverflowState.MISCONFIGURED, "is configured incorrectly"),
    ],
)
def test_every_unavailable_overflow_state_gets_an_explicit_verdict(
    state: OverflowState, expected: str
) -> None:
    choice = OverflowChoice(adapter_id="test", label="Test", state=state)

    assert expected in texts.overflow_unavailable(choice, max_mb=50)


class TestSizeLimitVerdictDetail:
    def test_the_observed_size_lands_in_parentheses(self) -> None:
        choice = OverflowChoice(adapter_id="none", label="none", state=OverflowState.OFF)
        verdict = texts.overflow_unavailable(choice, max_mb=50, observed="stopped at 63 MB")

        assert "50 MB limit (stopped at 63 MB)." in verdict

    def test_without_an_observation_there_are_no_empty_parentheses(self) -> None:
        choice = OverflowChoice(adapter_id="none", label="none", state=OverflowState.OFF)

        assert "()" not in texts.overflow_unavailable(choice, max_mb=50)


class TestDownloadingProgress:
    def test_each_stream_is_reported_under_its_own_name(self) -> None:
        assert texts.downloading_progress("video", "47%") == "Downloading video… 47%"
        assert texts.downloading_progress("audio", "12%") == "Downloading audio… 12%"

    def test_a_single_file_keeps_the_plain_wording(self) -> None:
        assert texts.downloading_progress("", "47%") == "Downloading… 47%"
