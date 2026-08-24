"""The owner's X session, kept where yt-dlp cannot damage it.

yt-dlp rewrites whatever cookie file it is handed — ``YoutubeDL.__exit__`` calls
``save_cookies()``, which rewrites the file after *every* run, successful or
not, and does so in place (open + truncate, no temp-and-rename). Handing it the
owner's export directly costs two things:

* the operator's own file gets mutated by a background service, and
* the file's mtime stops meaning "this is the session the owner exported",
  because the bot itself moves it on every download.

That second one matters more than it looks: the expiry alert dedupes on exactly
that marker (see docs/ARCHITECTURE.md D3), and a marker the bot keeps touching
would make the alert fire on every private tweet instead of once per export.

So the export is treated as read-only input, and each request gets its own
throwaway copy inside its own scratch directory. Per-request rather than one
shared working file, because a download abandoned on timeout keeps running for
a moment and rewrites its cookie file on the way out — which, with a shared
file, lands in the middle of the next request reading it.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_COPY_NAME = "cookies.txt"


class CookieSession:
    """Read-only view of the owner's cookie export, plus per-request copies."""

    def __init__(self, source: Path | None) -> None:
        self._source = source

    @property
    def configured(self) -> bool:
        return self._source is not None

    @property
    def source(self) -> Path | None:
        """The owner's export — what an alert should name, and what they replace."""
        return self._source

    def version(self) -> tuple[float, int] | None:
        """Identity of the current export: changes only when the owner replaces it.

        `None` when no cookie file is configured, or when it cannot be read —
        both are states the alert must treat as "same as before" rather than as
        a fresh session.
        """
        if self._source is None:
            return None
        try:
            stat = self._source.stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size)

    def stage_into(self, scratch: Path) -> Path | None:
        """Copy the export into one request's scratch directory for yt-dlp to chew on.

        Returns `None` — meaning "download anonymously" — when no export is
        configured or it cannot be read. Public tweets still work; anything
        private will fail and be reported as expired auth, which is the truth.
        """
        if self._source is None:
            return None
        copy = scratch / _COPY_NAME
        try:
            scratch.mkdir(parents=True, exist_ok=True)
            # Created 0600 before any bytes land in it, rather than copied and
            # then chmod'ed: the export is a credential, and the window between
            # the two is avoidable.
            copy.touch(mode=0o600, exist_ok=True)
            copy.chmod(0o600)
            shutil.copyfile(self._source, copy)
        except OSError as exc:
            logger.warning("could not stage cookies from %s: %s", self._source, exc)
            return None
        return copy
