"""Two concrete Providers in one module: no way to tell which one it is."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class FirstProvider(Provider):
    name: ClassVar[str] = "First"
    hint: ClassVar[str] = "first.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://first\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()


class SecondProvider(Provider):
    name: ClassVar[str] = "Second"
    hint: ClassVar[str] = "second.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://second\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
