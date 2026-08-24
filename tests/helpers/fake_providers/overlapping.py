"""Claims the same host as good.py but spells out more of the URL. Longest match must win."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class OverlappingProvider(Provider):
    name: ClassVar[str] = "Overlapping"
    hint: ClassVar[str] = "good.example/<id>/extra"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://good\.example/(?P<id>\d+)/extra")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
