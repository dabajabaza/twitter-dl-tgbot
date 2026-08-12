"""Turning yt-dlp's one exception type and one dict into things the bot can act on."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.utils import DownloadError

from twitter_dl.errors import (
    AuthExpired,
    DownloadFailed,
    NetworkUnavailable,
    NoVideoInTweet,
    TweetUnavailable,
)
from twitter_dl.services import downloader as module

TWEET = "https://x.com/someone/status/1234567890"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("NSFW tweet requires authentication", AuthExpired),
        ("Requested content is not available, please log in", AuthExpired),
        ("This tweet is age-restricted", AuthExpired),
        ("Your cookies are no longer valid", AuthExpired),
        ("Unable to download API page: <urlopen error proxy>", NetworkUnavailable),
        ("Connection refused", NetworkUnavailable),
        ("The read operation timed out", NetworkUnavailable),
        ("No video could be found in this tweet", NoVideoInTweet),
        ("Unsupported URL: https://x.com/someone", NoVideoInTweet),
        ("This account is suspended", TweetUnavailable),
        ("Tweet does not exist", TweetUnavailable),
        ("This account's Tweets are protected", TweetUnavailable),
        ("Some brand new failure mode", DownloadFailed),
    ],
)
def test_every_failure_is_sorted_into_a_class_the_worker_answers_for(
    message: str, expected: type[Exception]
) -> None:
    assert isinstance(module._classify(DownloadError(message)), expected)


def test_authentication_wins_over_availability_when_a_message_says_both() -> None:
    # An expired session usually presents as "not available" as well; treating
    # it as a missing tweet would leave the owner unaware their cookies died.
    error = DownloadError("Requested content is not available. Log in to see it.")
    assert isinstance(module._classify(error), AuthExpired)


class TestProgress:
    def test_a_known_total_is_reported_as_a_percentage(self) -> None:
        status = {"status": "downloading", "downloaded_bytes": 47, "total_bytes": 100}
        assert module._format_progress(status) == "47%"

    def test_an_unknown_total_falls_back_to_megabytes_done(self) -> None:
        status = {"status": "downloading", "downloaded_bytes": 3 * 1024 * 1024}
        assert module._format_progress(status) == "3.0 MB"

    def test_an_estimate_counts_as_a_total(self) -> None:
        status = {
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_bytes_estimate": 200,
        }
        assert module._format_progress(status) == "25%"

    def test_nothing_is_reported_for_states_that_are_not_progress(self) -> None:
        assert module._format_progress({"status": "finished"}) is None


class TestClipsFromInfo:
    def _entry(self, path: Path, **overrides: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": "1234567890",
            "uploader_id": "someone",
            "upload_date": "20260813",
            "requested_downloads": [{"filepath": str(path)}],
        }
        entry.update(overrides)
        return entry

    def test_a_single_video_tweet_yields_one_clip(self, tmp_path: Path) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")

        clips = module._clips_from_info(self._entry(video), TWEET)

        assert len(clips) == 1
        assert clips[0].path == video
        assert clips[0].tweet_id == "1234567890"
        assert clips[0].uploader == "someone"
        assert clips[0].upload_date == date(2026, 8, 13)

    def test_a_tweet_with_several_videos_yields_all_of_them(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
        first.write_bytes(b"x")
        second.write_bytes(b"y")
        info = {
            "_type": "playlist",
            "entries": [self._entry(first), self._entry(second)],
        }

        assert len(module._clips_from_info(info, TWEET)) == 2

    def test_a_tweet_with_no_media_is_not_a_mysterious_failure(self) -> None:
        with pytest.raises(NoVideoInTweet):
            module._clips_from_info(None, TWEET)

    def test_an_entry_whose_file_never_landed_is_not_reported_as_a_clip(
        self, tmp_path: Path
    ) -> None:
        missing = self._entry(tmp_path / "gone.mp4")

        with pytest.raises(NoVideoInTweet):
            module._clips_from_info(missing, TWEET)

    def test_a_missing_upload_date_falls_back_to_today_rather_than_failing(
        self, tmp_path: Path
    ) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")

        clips = module._clips_from_info(self._entry(video, upload_date=None), TWEET)

        assert clips[0].upload_date == date.today()
