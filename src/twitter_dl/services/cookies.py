"""The owner's X session, kept where yt-dlp cannot damage it.

yt-dlp rewrites whatever cookie file it is handed — ``YoutubeDL.__exit__`` calls
``save_cookies()``, which rewrites the file after *every* run, successful or not.
Handing it the owner's export directly costs two things:

* the operator's own file gets mutated by a background service, and
* the file's mtime stops meaning "this is the session the owner exported",
  because the bot itself moves it on every download.

That second one matters more than it looks: the expiry alert dedupes on exactly
that marker (see docs/ARCHITECTURE.md D3), and a marker the bot keeps touching
would make the alert fire on every private tweet instead of once per export.

So the export is treated as read-only input, copied into a working file that
yt-dlp is free to rewrite, and refreshed whenever the owner replaces the export.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_WORKING_NAME = "cookies-working.txt"


class CookieSession:
    """Read-only view of the owner's cookie export, plus a scratch copy for yt-dlp."""

    def __init__(self, source: Path | None, *, workdir: Path) -> None:
        self._source = source
        self._working = workdir / _WORKING_NAME
        self._copied_from: tuple[float, int] | None = None

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
        both are states the alert should treat as "same as before" rather than
        as a fresh session.
        """
        if self._source is None:
            return None
        try:
            stat = self._source.stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size)

    def path_for_download(self) -> Path | None:
        """The file to hand yt-dlp, refreshed if the owner replaced the export."""
        if self._source is None:
            return None
        version = self.version()
        if version is None:
            logger.warning("cookie file %s is configured but unreadable", self._source)
            return None
        if version != self._copied_from or not self._working.exists():
            try:
                self._working.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self._source, self._working)
                self._working.chmod(0o600)
            except OSError as exc:
                logger.warning("could not stage cookies from %s: %s", self._source, exc)
                return None
            self._copied_from = version
            logger.info("staged a fresh cookie session from %s", self._source)
        return self._working
