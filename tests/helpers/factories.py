"""Building the objects tests need, with the same types production uses."""

from datetime import date
from pathlib import Path
from typing import Any

from twitter_dl.config import Settings
from twitter_dl.domain import Clip

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
        "overflow_adapters": {},
        "overflow_default": "none",
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
    tweet_id: str = "1234567890",
    uploader: str = "someone",
    upload_date: date = date(2026, 8, 13),
    name: str = "clip.mp4",
) -> Clip:
    path = tmp_path / name
    path.write_bytes(b"\0" * size_bytes)
    return Clip(path=path, tweet_id=tweet_id, uploader=uploader, upload_date=upload_date)
