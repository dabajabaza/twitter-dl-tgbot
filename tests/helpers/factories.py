"""Building the objects tests need, with the same types production uses."""

import re
from datetime import date
from pathlib import Path
from typing import Any

from clipivore.config import Settings
from clipivore.domain import Clip, Downloader
from clipivore.services.providers import Provider, ProviderChoice, ProviderContext, ProviderState

_POST_LINK = re.compile(r"https?://x\.com/[^/]+/status/(?P<id>\d+)")

# Shaped like a real token so aiogram's own validation passes; allowlisted in
# .gitleaks.toml so the secret scanner does not trip over it.
FAKE_BOT_TOKEN = "123456:" + "A" * 35

OWNER_ID = 1
GUEST_ID = 2
STRANGER_ID = 99


def build_settings(tmp_path: Path, **overrides: Any) -> Settings:
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    values: dict[str, Any] = {
        "bot_token": FAKE_BOT_TOKEN,
        "owner_id": OWNER_ID,
        "allowed_ids_raw": str(GUEST_ID),
        "download_dir": downloads,
        "queue_limit": 5,
        "download_timeout_s": 1800,
        "max_tg_video_mb": 50,
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)


def make_clip(
    tmp_path: Path,
    *,
    size_bytes: int = 1024,
    post_id: str = "1234567890",
    uploader: str = "someone",
    upload_date: date = date(2026, 8, 13),
    name: str = "clip.mp4",
    description: str = "",
    uploader_url: str = "",
) -> Clip:
    path = tmp_path / name
    path.write_bytes(b"\0" * size_bytes)
    return Clip(
        path=path,
        post_id=post_id,
        uploader=uploader,
        upload_date=upload_date,
        description=description,
        uploader_url=uploader_url,
    )


def make_provider_choice(
    *,
    downloader: Downloader | None = None,
    provider_id: str = "x",
    name: str = "X",
    cookies: object | None = None,
    ready: bool = True,
) -> ProviderChoice:
    """A Provider the worker can be driven with, without importing an engine.

    Built around the real `Provider` base so the worker sees the production
    shape; the downloader is whatever the test wants to observe.
    """

    class _TestProvider(Provider):
        name = "X"
        hint = "test provider"
        post_link = _POST_LINK

        def __init__(self, context: ProviderContext) -> None:
            self.cookies = cookies

        @property
        def downloader(self) -> Downloader:
            assert downloader is not None
            return downloader

    _TestProvider.name = name
    return ProviderChoice(
        provider_id=provider_id,
        name=name,
        state=ProviderState.READY if ready else ProviderState.MISCONFIGURED,
        cls=_TestProvider,
        provider=_TestProvider(ProviderContext()) if ready else None,
        error="" if ready else "built to be broken",
    )
