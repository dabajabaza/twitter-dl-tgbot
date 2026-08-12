"""The owner's export is input; each request gets a copy it may scribble on."""

from pathlib import Path

from twitter_dl.services.cookies import CookieSession


def build(tmp_path: Path, body: str = "netscape cookies") -> tuple[CookieSession, Path]:
    export = tmp_path / "cookies.txt"
    export.write_text(body)
    return CookieSession(export), export


def test_yt_dlp_is_handed_a_copy_never_the_owners_file(tmp_path: Path) -> None:
    cookies, export = build(tmp_path)

    staged = cookies.stage_into(tmp_path / "req-1")

    assert staged is not None
    assert staged != export
    assert staged.read_text() == export.read_text()


def test_the_copy_is_never_readable_by_anyone_else(tmp_path: Path) -> None:
    cookies, _ = build(tmp_path)

    staged = cookies.stage_into(tmp_path / "req-1")

    assert staged is not None
    assert staged.stat().st_mode & 0o077 == 0


def test_each_request_gets_its_own_copy(tmp_path: Path) -> None:
    # A download abandoned on timeout keeps running for a moment and rewrites
    # its cookie file on the way out. With one shared working file that write
    # lands in the middle of the next request reading it, and the next request
    # then looks unauthenticated — a false "your cookies died" to the owner.
    cookies, _ = build(tmp_path)

    first = cookies.stage_into(tmp_path / "req-1")
    second = cookies.stage_into(tmp_path / "req-2")

    assert first is not None and second is not None
    assert first != second

    first.write_text("rewritten by an abandoned download")
    assert second.read_text() == "netscape cookies"


def test_rewriting_a_copy_does_not_change_the_session_identity(tmp_path: Path) -> None:
    # This is what yt-dlp does after every run; it must not read as a new
    # session, or the expiry alert would fire on every single download.
    cookies, _ = build(tmp_path)
    before = cookies.version()

    staged = cookies.stage_into(tmp_path / "req-1")
    assert staged is not None
    staged.write_text("rewritten by yt-dlp")

    assert cookies.version() == before


def test_replacing_the_export_changes_the_identity_and_the_copies(tmp_path: Path) -> None:
    cookies, export = build(tmp_path)
    before = cookies.version()

    export.write_text("a genuinely fresh export")

    assert cookies.version() != before
    staged = cookies.stage_into(tmp_path / "req-2")
    assert staged is not None
    assert staged.read_text() == "a genuinely fresh export"


def test_no_cookie_file_configured_is_not_an_error(tmp_path: Path) -> None:
    cookies = CookieSession(None)

    assert not cookies.configured
    assert cookies.stage_into(tmp_path / "req-1") is None
    assert cookies.version() is None


def test_a_missing_export_downgrades_to_anonymous_rather_than_crashing(tmp_path: Path) -> None:
    # Public tweets still download; the owner sees a warning in the log.
    cookies = CookieSession(tmp_path / "nope.txt")

    assert cookies.configured
    assert cookies.stage_into(tmp_path / "req-1") is None
    assert cookies.version() is None
