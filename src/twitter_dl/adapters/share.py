"""Overflow delivery to a filesystem-like rclone remote."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict

from twitter_dl.adapters._rclone import RcloneSettings, run_rclone
from twitter_dl.services.overflow import OverflowDestination


class ShareSettings(RcloneSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SHARE_",
        extra="ignore",
    )

    rclone_remote: str
    path_prefix: str

    @field_validator("path_prefix")
    @classmethod
    def _nonempty_path_prefix(cls, value: str) -> str:
        prefix = value.strip().rstrip("\\/")
        if not prefix:
            raise ValueError("path prefix must not be empty")
        return prefix


class ShareDestination(OverflowDestination):
    label = "Share"

    def __init__(self, settings: ShareSettings) -> None:
        self._settings = settings

    async def store(self, source: Path, *, name: str) -> str:
        await run_rclone(
            self._settings,
            "--no-traverse",
            "--inplace",
            "copyto",
            str(source),
            f"{self._settings.rclone_remote}/{name}",
        )
        return f"{self._settings.path_prefix}\\{name}"


def create() -> ShareDestination:
    return ShareDestination(ShareSettings())
