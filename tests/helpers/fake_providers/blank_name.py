"""A nameless Provider could not be named in a verdict, so it is not one."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class NamelessProvider(Provider):
    name: ClassVar[str] = "   "
    hint: ClassVar[str] = "nameless.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://nameless\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
