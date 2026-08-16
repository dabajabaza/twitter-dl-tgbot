"""Turning yt-dlp's one exception type and one dict into things the bot can act on."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import DownloadError

from twitter_dl.errors import (
    AuthExpired,
    DownloadFailed,
    DownloadTooLarge,
    NetworkUnavailable,
    NoVideoInTweet,
    TweetUnavailable,
)
from twitter_dl.services import downloader as module

LOGIN_HINT = InfoExtractor._login_hint(InfoExtractor)

TWEET = "https://x.com/someone/status/1234567890"


# The exact sentences the X extractor raises, read out of its source rather
# than imagined: a taxonomy tested against invented strings certifies nothing.
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # raise_login_required() in the extractor, plus the boilerplate hint
        # yt-dlp appends to every login-required error.
        (f"NSFW tweet requires authentication. {LOGIN_HINT}", AuthExpired),
        (f"This video is only available for registered users. {LOGIN_HINT}", AuthExpired),
        # Same shape, but the account state is what blocks us — no credential
        # of ours would help, so this must NOT read as expired cookies.
        (f"You are not authorized to view this protected tweet. {LOGIN_HINT}", TweetUnavailable),
        ("This account is suspended", TweetUnavailable),
        ("Broadcast no longer exists", TweetUnavailable),
        ("Twitter Space not found", TweetUnavailable),
        # raise_no_formats() / restriction to X extractors.
        ("No video could be found in this tweet", NoVideoInTweet),
        ("Media #1 is not a video", NoVideoInTweet),
        ("No suitable extractor found for URL https://youtube.com/watch?v=x", NoVideoInTweet),
        ("Video #1 is unavailable", TweetUnavailable),
        # Transport, not content.
        ("Unable to download API page: <urlopen error proxy>", NetworkUnavailable),
        ("Connection refused", NetworkUnavailable),
        ("The read operation timed out", NetworkUnavailable),
        ("Some brand new failure mode", DownloadFailed),
    ],
)
def test_every_failure_is_sorted_into_a_class_the_worker_answers_for(
    message: str, expected: type[Exception]
) -> None:
    assert isinstance(module._classify(DownloadError(message)), expected)


def test_the_cookie_boilerplate_alone_never_means_our_session_died() -> None:
    # The hint contains both "authenticat" and "cookies", so matching on the
    # raw message made every login-required error look like expired cookies —
    # and woke the owner over tweets no session of theirs could ever open.
    assert "authenticat" in LOGIN_HINT.lower()
    assert "cookies" in LOGIN_HINT.lower()
    stripped = module._strip_login_hint(f"This account is suspended. {LOGIN_HINT}")
    assert "cookies" not in stripped.lower()


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


class TestDownloadCeiling:
    async def test_a_known_oversized_stream_is_refused_before_its_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloader = module.YtDlpDownloader()

        def fake_extract(
            url: str,
            dest: Path,
            hook: Any,
            abandoned: Any,
            max_bytes: int | None,
            limit_hit: list[int],
        ) -> Any:
            options = downloader._options(
                dest,
                hook,
                max_bytes=max_bytes,
                limit_hit=limit_hit,
            )
            assert options["max_filesize"] == 50
            options["match_filter"]({"filesize": 51}, incomplete=False)

        monkeypatch.setattr(downloader, "_extract", fake_extract)

        with pytest.raises(DownloadTooLarge) as raised:
            await downloader.download(TWEET, tmp_path, max_bytes=50)
        assert raised.value.observed_bytes == 51

    async def test_an_unknown_stream_is_stopped_as_soon_as_received_bytes_cross_the_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloader = module.YtDlpDownloader()

        def fake_extract(
            url: str,
            dest: Path,
            hook: Any,
            abandoned: Any,
            max_bytes: int | None,
            limit_hit: list[int],
        ) -> Any:
            hook({"status": "downloading", "downloaded_bytes": 51})

        monkeypatch.setattr(downloader, "_extract", fake_extract)

        with pytest.raises(DownloadTooLarge):
            await downloader.download(TWEET, tmp_path, max_bytes=50)

    async def test_separate_streams_share_one_received_byte_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloader = module.YtDlpDownloader()

        def fake_extract(
            url: str,
            dest: Path,
            hook: Any,
            abandoned: Any,
            max_bytes: int | None,
            limit_hit: list[int],
        ) -> Any:
            hook(
                {
                    "status": "downloading",
                    "info_dict": {"id": "clip-1", "format_id": "video"},
                    "filename": "video.part",
                    "downloaded_bytes": 40,
                }
            )
            hook(
                {
                    "status": "downloading",
                    "info_dict": {"id": "clip-1", "format_id": "audio"},
                    "filename": "audio.part",
                    "downloaded_bytes": 11,
                }
            )

        monkeypatch.setattr(downloader, "_extract", fake_extract)

        with pytest.raises(DownloadTooLarge) as raised:
            await downloader.download(TWEET, tmp_path, max_bytes=50)
        assert raised.value.observed_bytes == 51

    async def test_each_clip_has_its_own_received_byte_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloader = module.YtDlpDownloader()
        first = tmp_path / "first.mp4"
        second = tmp_path / "second.mp4"
        first.write_bytes(b"1")
        second.write_bytes(b"2")

        def fake_extract(
            url: str,
            dest: Path,
            hook: Any,
            abandoned: Any,
            max_bytes: int | None,
            limit_hit: list[int],
        ) -> Any:
            hook(
                {
                    "status": "downloading",
                    "info_dict": {"id": "clip-1", "format_id": "video"},
                    "downloaded_bytes": 30,
                }
            )
            hook(
                {
                    "status": "downloading",
                    "info_dict": {"id": "clip-2", "format_id": "video"},
                    "downloaded_bytes": 30,
                }
            )
            return {
                "_type": "playlist",
                "entries": [
                    {
                        "id": "clip-1",
                        "requested_downloads": [{"filepath": str(first)}],
                    },
                    {
                        "id": "clip-2",
                        "requested_downloads": [{"filepath": str(second)}],
                    },
                ],
            }

        monkeypatch.setattr(downloader, "_extract", fake_extract)

        clips = await downloader.download(TWEET, tmp_path, max_bytes=50)

        assert len(clips) == 2

    def test_exact_sizes_of_selected_streams_are_summed(self, tmp_path: Path) -> None:
        hits: list[int] = []
        options = module.YtDlpDownloader()._options(
            tmp_path,
            lambda status: None,
            max_bytes=50,
            limit_hit=hits,
        )

        with pytest.raises(module._Abandoned):
            options["match_filter"](
                {"requested_formats": [{"filesize": 40}, {"filesize": 20}]},
                incomplete=False,
            )
        assert hits == [60]

    def test_content_length_refusal_raises_the_typed_exit_immediately(self, tmp_path: Path) -> None:
        hits: list[int] = []
        options = module.YtDlpDownloader()._options(
            tmp_path,
            lambda status: None,
            max_bytes=50,
            limit_hit=hits,
        )

        content_length = 51
        with pytest.raises(module._Abandoned):
            _ = content_length > options["max_filesize"]

        assert hits == [51]


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


class TestStayingOnX:
    """A tweet is the only thing this bot is allowed to download."""

    def test_only_x_extractors_are_enabled(self, tmp_path: Path) -> None:
        # A tweet with no media but an outbound link makes the extractor follow
        # that link. Without this restriction the bot would fetch a stranger's
        # video through the owner's proxy and file it externally as theirs.
        options = module.YtDlpDownloader()._options(tmp_path, lambda status: None)

        from yt_dlp import YoutubeDL

        with YoutubeDL({**options, "logger": None}) as ydl:
            names = {ie.IE_NAME for ie in ydl._ies.values()}
        assert names and all(name.startswith("twitter") for name in names)

    def test_a_link_off_x_is_reported_as_a_tweet_without_video(self) -> None:
        error = DownloadError("ERROR: No suitable extractor found for URL https://youtube.com/x")
        assert isinstance(module._classify(error), NoVideoInTweet)


class TestCookiesAreACopy:
    def test_the_working_copy_is_what_yt_dlp_is_pointed_at(self, tmp_path: Path) -> None:
        from twitter_dl.services.cookies import CookieSession

        export = tmp_path / "cookies.txt"
        export.write_text("netscape")
        cookies = CookieSession(export)

        scratch = tmp_path / "req-1"
        options = module.YtDlpDownloader(cookies=cookies)._options(scratch, lambda s: None)

        assert options["cookiefile"] != str(export)
        # Inside this request's own scratch, so an abandoned download cannot
        # rewrite the file the next request is reading.
        assert Path(options["cookiefile"]).parent == scratch
        assert Path(options["cookiefile"]).read_text() == "netscape"

    def test_without_cookies_the_option_is_absent_rather_than_empty(self, tmp_path: Path) -> None:
        options = module.YtDlpDownloader()._options(tmp_path, lambda s: None)
        assert "cookiefile" not in options


class TestTweetIdentity:
    def test_the_id_from_the_link_wins_over_the_id_of_the_media(self, tmp_path: Path) -> None:
        # The extractor puts the media object's id in `id` and the tweet's own
        # id in `display_id`. External names are looked up from the link.
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        entry = {
            "id": "1575559336759263233",  # media
            "display_id": "1575560063510810624",  # the tweet
            "uploader_id": "someone",
            "upload_date": "20260813",
            "requested_downloads": [{"filepath": str(video)}],
        }

        clips = module._clips_from_info(entry, TWEET)

        assert clips[0].tweet_id == "1575560063510810624"
