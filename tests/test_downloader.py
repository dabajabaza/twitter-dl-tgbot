"""Turning yt-dlp's one exception type and one dict into things the bot can act on."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import DownloadError

from clipivore.domain import Clip
from clipivore.errors import (
    AuthExpired,
    DownloadFailed,
    DownloadTooLarge,
    NetworkUnavailable,
    NoVideoInPost,
    PostUnavailable,
)
from clipivore.providers import twitter as twitter_provider
from clipivore.services import downloader as module
from clipivore.services.providers import ProviderCatalog, ProviderChoice, ProviderContext

LOGIN_HINT = InfoExtractor._login_hint(InfoExtractor)

TWEET = "https://x.com/someone/status/1234567890"

# The real thing, not a stand-in: these tests are about how Twitter's own sentences
# and metadata are read, so a hand-written profile would test the test. Read as
# a declaration rather than built from a constructed Provider, so collecting
# this module cannot depend on what is in the environment.
TWITTER_PROFILE = twitter_provider.PROFILE


def clips_from_info(info: object, url: str) -> list[Clip]:
    return module._clips_from_info(info, url, TWITTER_PROFILE)


def engine_of(choice: ProviderChoice) -> module.YtDlpDownloader:
    """The concrete engine behind a Provider, for the tests that inspect options."""
    assert choice.provider is not None
    downloader = choice.provider.downloader
    assert isinstance(downloader, module.YtDlpDownloader)
    return downloader


# The exact sentences the Twitter extractor raises, read out of its source rather
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
        (f"You are not authorized to view this protected tweet. {LOGIN_HINT}", PostUnavailable),
        ("This account is suspended", PostUnavailable),
        ("Broadcast no longer exists", PostUnavailable),
        ("Twitter Space not found", PostUnavailable),
        # raise_no_formats() / restriction to Twitter extractors.
        ("No video could be found in this tweet", NoVideoInPost),
        ("Media #1 is not a video", NoVideoInPost),
        ("No suitable extractor found for URL https://youtube.com/watch?v=x", NoVideoInPost),
        ("Video #1 is unavailable", PostUnavailable),
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
    assert isinstance(module._classify(DownloadError(message), TWITTER_PROFILE), expected)


def test_the_cookie_boilerplate_alone_never_means_our_session_died() -> None:
    # The hint contains both "authenticat" and "cookies", so matching on the
    # raw message made every login-required error look like expired cookies —
    # and woke the owner over tweets no session of theirs could ever open.
    assert "authenticat" in LOGIN_HINT.lower()
    assert "cookies" in LOGIN_HINT.lower()
    stripped = module._strip_login_hint(f"This account is suspended. {LOGIN_HINT}")
    assert "cookies" not in stripped.lower()


class TestProgress:
    def test_a_known_total_is_reported_as_a_percentage_of_a_human_size(self) -> None:
        status = {
            "status": "downloading",
            "downloaded_bytes": 41 * 1024 * 1024,
            "total_bytes": 82 * 1024 * 1024,
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.text == "50% of 82 MB"

    def test_an_unknown_total_falls_back_to_megabytes_done(self) -> None:
        status = {"status": "downloading", "downloaded_bytes": 3 * 1024 * 1024}
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.text == "3.0 MB"

    def test_an_estimate_counts_as_a_total(self) -> None:
        status = {
            "status": "downloading",
            "downloaded_bytes": 1024 * 1024,
            "total_bytes_estimate": 4 * 1024 * 1024,
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.text == "25% of 4 MB"

    def test_nothing_is_reported_for_states_that_are_not_progress(self) -> None:
        assert module._format_progress({"status": "finished"}) is None

    def test_a_video_only_stream_is_named_video(self) -> None:
        status = {
            "status": "downloading",
            "downloaded_bytes": 1,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.stream == "video"

    def test_an_audio_only_stream_is_named_audio(self) -> None:
        # The realistic Twitter shape: an HLS EXT-X-MEDIA audio rendition, for which
        # yt-dlp sets vcodec="none" but never fills acodec at all. Keying on
        # acodec would leave every real audio run nameless.
        status = {
            "status": "downloading",
            "downloaded_bytes": 1,
            "info_dict": {"vcodec": "none", "ext": "mp4"},
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.stream == "audio"

    def test_an_audio_stream_with_a_known_codec_is_still_audio(self) -> None:
        status = {
            "status": "downloading",
            "downloaded_bytes": 1,
            "info_dict": {"vcodec": "none", "acodec": "mp4a"},
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.stream == "audio"

    def test_a_single_combined_file_gets_no_stream_name(self) -> None:
        # The /b fallback (Twitter GIFs, no ffmpeg) downloads one file with both
        # codecs; naming it "video" would promise an audio run that never comes.
        status = {
            "status": "downloading",
            "downloaded_bytes": 1,
            "info_dict": {"vcodec": "avc1", "acodec": "mp4a"},
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.stream == ""

    def test_unknown_codecs_get_no_stream_name(self) -> None:
        status = {"status": "downloading", "downloaded_bytes": 1}
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.stream == ""

    def test_a_sub_megabyte_total_is_reported_in_kilobytes(self) -> None:
        # The audio stream of a short clip is a few hundred KB; "40% of 0 MB"
        # would read as a glitch.
        status = {
            "status": "downloading",
            "downloaded_bytes": 80 * 1024,
            "total_bytes": 200 * 1024,
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.text == "40% of 200 KB"

    def test_a_gigabyte_total_is_reported_in_gigabytes(self) -> None:
        status = {
            "status": "downloading",
            "downloaded_bytes": 1024 * 1024 * 1024,
            "total_bytes": 2 * 1024 * 1024 * 1024,
        }
        progress = module._format_progress(status)
        assert progress is not None
        assert progress.text == "50% of 2.0 GB"


class TestDownloadCeiling:
    async def test_a_known_oversized_stream_is_refused_before_its_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloader = module.YtDlpDownloader(TWITTER_PROFILE)

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
        downloader = module.YtDlpDownloader(TWITTER_PROFILE)

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
        downloader = module.YtDlpDownloader(TWITTER_PROFILE)

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
        downloader = module.YtDlpDownloader(TWITTER_PROFILE)
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
        options = module.YtDlpDownloader(TWITTER_PROFILE)._options(
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
        options = module.YtDlpDownloader(TWITTER_PROFILE)._options(
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

        clips = clips_from_info(self._entry(video), TWEET)

        assert len(clips) == 1
        assert clips[0].path == video
        assert clips[0].post_id == "1234567890"
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

        assert len(clips_from_info(info, TWEET)) == 2

    def test_a_tweet_with_no_media_is_not_a_mysterious_failure(self) -> None:
        with pytest.raises(NoVideoInPost):
            clips_from_info(None, TWEET)

    def test_an_entry_whose_file_never_landed_is_not_reported_as_a_clip(
        self, tmp_path: Path
    ) -> None:
        missing = self._entry(tmp_path / "gone.mp4")

        with pytest.raises(NoVideoInPost):
            clips_from_info(missing, TWEET)

    def test_a_missing_upload_date_falls_back_to_today_rather_than_failing(
        self, tmp_path: Path
    ) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")

        clips = clips_from_info(self._entry(video, upload_date=None), TWEET)

        assert clips[0].upload_date == date.today()

    def test_the_tweets_text_survives_into_the_clip(self, tmp_path: Path) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        entry = self._entry(video, description="Cats!", uploader_url="https://x.com/someone")

        clips = clips_from_info(entry, TWEET)

        assert clips[0].description == "Cats!"
        assert clips[0].uploader_url == "https://x.com/someone"

    def test_the_trailing_media_pointer_is_stripped_from_the_text(self, tmp_path: Path) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        # Twitter appends its own t.co link to the media; both schemes occur, and a
        # tweet with several media gets several. One in mid-sentence is the
        # author's own words and stays.
        entry = self._entry(
            video,
            description="see https://t.co/InThEmIdDle ok https://t.co/AbC123 http://t.co/dEf456",
        )

        clips = clips_from_info(entry, TWEET)

        assert clips[0].description == "see https://t.co/InThEmIdDle ok"

    def test_a_tweet_without_text_yields_an_empty_description(self, tmp_path: Path) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")

        clips = clips_from_info(self._entry(video, description=None), TWEET)

        assert clips[0].description == ""

    def test_a_missing_profile_url_is_rebuilt_from_the_handle(self, tmp_path: Path) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")

        clips = clips_from_info(self._entry(video), TWEET)

        assert clips[0].uploader_url == "https://x.com/someone"

    def test_no_handle_at_all_leaves_the_profile_url_empty(self, tmp_path: Path) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        entry = self._entry(video, uploader_id=None, uploader="Some One")

        clips = clips_from_info(entry, TWEET)

        assert clips[0].uploader_url == ""


class TestTransportFailuresAreNotThePlatformsFault:
    """A 5xx says the platform's machinery broke, not that the post is gone."""

    @pytest.mark.parametrize(
        "message",
        [
            "Unable to download JSON metadata: HTTP Error 503: Service Unavailable",
            "Unable to download JSON metadata: HTTP Error 502: Bad Gateway",
            "HTTP Error 500: Internal Server Error",
        ],
    )
    def test_a_server_error_reads_as_the_network_not_as_a_missing_post(self, message: str) -> None:
        # "503 Service Unavailable" contains the word "unavailable", so before
        # this it told somebody their post had been deleted every time the API
        # hiccupped.
        classified = module._classify(DownloadError(message), TWITTER_PROFILE)
        assert isinstance(classified, NetworkUnavailable)

    def test_a_4xx_is_still_left_to_the_platform_to_explain(self) -> None:
        # Only 5xx: a 4xx is about this request, and the platform's own markers
        # are what read it.
        error = DownloadError("HTTP Error 404: Not Found")
        assert isinstance(module._classify(error, TWITTER_PROFILE), PostUnavailable)


class TestTheIdInAnExternalNameComesFromTheLink:
    """D7: a stored clip has to be findable from the link somebody sent."""

    def test_a_provider_that_reads_the_id_itself_overrides_the_extractor(
        self, tmp_path: Path
    ) -> None:
        # A Bluesky quote post reports the *quoted* post's id, so the file would
        # otherwise be named after a post the sender never saw.
        video = tmp_path / "quoted.mp4"
        video.write_bytes(b"x")
        entry = {
            "id": "3l3vgf77uco2g",  # the quoted post
            "uploader_id": "bsky.app",
            "upload_date": "20240911",
            "requested_downloads": [{"filepath": str(video)}],
        }
        profile = module.EngineProfile(
            allowed_extractors=("bluesky",),
            post_id_from_url=lambda url: "3l6oe5mtr2c2j",
        )

        clips = module._clips_from_info(entry, "https://bsky.app/…/post/3l6oe5mtr2c2j", profile)

        assert clips[0].post_id == "3l6oe5mtr2c2j"

    def test_without_the_hook_the_extractor_still_decides(self, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        entry = {
            "id": "media-1",
            "display_id": "1234567890",
            "uploader_id": "someone",
            "upload_date": "20260813",
            "requested_downloads": [{"filepath": str(video)}],
        }

        clips = clips_from_info(entry, TWEET)

        assert clips[0].post_id == "1234567890"


class TestEveryProviderStaysOnItsOwnPlatform:
    """One platform's post is the only thing its Provider may download.

    Per Provider, never a union: a union would let a post on one platform
    redirect into another platform's extractor, which is exactly the hole the
    allowlist exists to close.
    """

    def test_each_provider_enables_only_its_own_extractors(self, tmp_path: Path) -> None:
        # A post with no media but an outbound link makes the extractor follow
        # that link. Without this restriction the bot would fetch a stranger's
        # video through the owner's proxy and file it externally as theirs.
        from yt_dlp import YoutubeDL

        for choice in ProviderCatalog(ProviderContext()).ready:
            downloader = engine_of(choice)
            profile = downloader._profile
            options = downloader._options(tmp_path, lambda status: None)
            with YoutubeDL({**options, "logger": None}) as ydl:
                names = {ie.IE_NAME.lower() for ie in ydl._ies.values()}
            assert names, f"{choice.name} enabled no extractor at all"
            allowed = [pattern.rstrip(".*") for pattern in profile.allowed_extractors]
            assert all(any(name.startswith(prefix) for prefix in allowed) for name in names), (
                f"{choice.name} enabled a foreign extractor: {sorted(names)}"
            )

    def test_one_providers_extractors_are_never_another_s(self, tmp_path: Path) -> None:
        from yt_dlp import YoutubeDL

        enabled: dict[str, set[str]] = {}
        for choice in ProviderCatalog(ProviderContext()).ready:
            options = engine_of(choice)._options(tmp_path, lambda status: None)
            with YoutubeDL({**options, "logger": None}) as ydl:
                enabled[choice.name] = {ie.IE_NAME.lower() for ie in ydl._ies.values()}
        for name, names in enabled.items():
            for other, other_names in enabled.items():
                if name != other:
                    assert not names & other_names, f"{name} and {other} share extractors"

    def test_a_link_off_the_platform_is_reported_as_a_post_without_video(self) -> None:
        error = DownloadError("ERROR: No suitable extractor found for URL https://youtube.com/x")
        assert isinstance(module._classify(error, TWITTER_PROFILE), NoVideoInPost)


class TestCookiesAreACopy:
    def test_the_working_copy_is_what_yt_dlp_is_pointed_at(self, tmp_path: Path) -> None:
        from clipivore.services.cookies import CookieSession

        export = tmp_path / "cookies.txt"
        export.write_text("netscape")
        cookies = CookieSession(export)

        scratch = tmp_path / "req-1"
        options = module.YtDlpDownloader(TWITTER_PROFILE, cookies=cookies)._options(
            scratch, lambda s: None
        )

        assert options["cookiefile"] != str(export)
        # Inside this request's own scratch, so an abandoned download cannot
        # rewrite the file the next request is reading.
        assert Path(options["cookiefile"]).parent == scratch
        assert Path(options["cookiefile"]).read_text() == "netscape"

    def test_without_cookies_the_option_is_absent_rather_than_empty(self, tmp_path: Path) -> None:
        options = module.YtDlpDownloader(TWITTER_PROFILE)._options(tmp_path, lambda s: None)
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

        clips = clips_from_info(entry, TWEET)

        assert clips[0].post_id == "1575560063510810624"
