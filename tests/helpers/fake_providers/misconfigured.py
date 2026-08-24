"""Constructs badly: still claims its links, so the refusal can name it."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class BrokenProvider(Provider):
    name: ClassVar[str] = "Broken"
    hint: ClassVar[str] = "broken.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://broken\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        raise ValueError("no credentials configured")

    @property
    def downloader(self) -> Downloader:
        return _Engine()
