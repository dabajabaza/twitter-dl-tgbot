"""Underscore modules are shared helpers: discovery must not open this one."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class HelperProvider(Provider):
    name: ClassVar[str] = "Helper"
    hint: ClassVar[str] = "helper.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://helper\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
