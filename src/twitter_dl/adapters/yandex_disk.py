"""Overflow delivery to Yandex Disk through rclone."""

from pathlib import Path

from pydantic_settings import SettingsConfigDict

from twitter_dl.adapters._rclone import RcloneSettings, run_rclone
from twitter_dl.services.overflow import OverflowDestination


class YandexDiskSettings(RcloneSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="YANDEX_DISK_",
        extra="ignore",
    )

    rclone_remote: str


class YandexDiskDestination(OverflowDestination):
    label = "Yandex Disk"

    def __init__(self, settings: YandexDiskSettings) -> None:
        self._settings = settings

    async def store(self, source: Path, *, name: str) -> str:
        remote_path = f"{self._settings.rclone_remote}/{name}"
        await run_rclone(
            self._settings,
            "--no-traverse",
            "copyto",
            str(source),
            remote_path,
        )
        link = await run_rclone(self._settings, "link", remote_path)
        if not link:
            raise RuntimeError("rclone link returned no public URL")
        return link.splitlines()[-1]


def create() -> YandexDiskDestination:
    return YandexDiskDestination(YandexDiskSettings())
