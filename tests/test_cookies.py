"""The owner's export is input; yt-dlp gets a copy it may scribble on."""

from pathlib import Path

from twitter_dl.services.cookies import CookieSession


def build(tmp_path: Path, body: str = "netscape cookies") -> tuple[CookieSession, Path]:
    export = tmp_path / "cookies.txt"
    export.write_text(body)
    return CookieSession(export, workdir=tmp_path / "work"), export


def test_yt_dlp_is_handed_a_copy_never_the_owners_file(tmp_path: Path) -> None:
    cookies, export = build(tmp_path)

    working = cookies.path_for_download()

    assert working is not None
    assert working != export
    assert working.read_text() == export.read_text()


def test_the_copy_is_not_world_readable(tmp_path: Path) -> None:
    cookies, _ = build(tmp_path)

    working = cookies.path_for_download()

    assert working is not None
    assert working.stat().st_mode & 0o077 == 0


def test_rewriting_the_copy_does_not_change_the_session_identity(tmp_path: Path) -> None:
    # This is what yt-dlp does after every run; it must not read as a new
    # session, or the expiry alert would fire on every single download.
    cookies, _ = build(tmp_path)
    before = cookies.version()
    working = cookies.path_for_download()
    assert working is not None

    working.write_text("rewritten by yt-dlp")

    assert cookies.version() == before


def test_the_copy_is_not_restaged_while_the_export_is_untouched(tmp_path: Path) -> None:
    cookies, _ = build(tmp_path)
    working = cookies.path_for_download()
    assert working is not None
    working.write_text("rewritten by yt-dlp")

    assert cookies.path_for_download() == working
    assert working.read_text() == "rewritten by yt-dlp"


def test_replacing_the_export_restages_the_copy_and_changes_identity(tmp_path: Path) -> None:
    cookies, export = build(tmp_path)
    cookies.path_for_download()
    before = cookies.version()

    export.write_text("a genuinely fresh export")

    assert cookies.version() != before
    working = cookies.path_for_download()
    assert working is not None
    assert working.read_text() == "a genuinely fresh export"


def test_no_cookie_file_configured_is_not_an_error(tmp_path: Path) -> None:
    cookies = CookieSession(None, workdir=tmp_path)

    assert not cookies.configured
    assert cookies.path_for_download() is None
    assert cookies.version() is None


def test_a_missing_export_downgrades_to_anonymous_rather_than_crashing(tmp_path: Path) -> None:
    # Public tweets still download; the owner sees a warning in the log.
    cookies = CookieSession(tmp_path / "nope.txt", workdir=tmp_path / "work")

    assert cookies.configured
    assert cookies.path_for_download() is None
