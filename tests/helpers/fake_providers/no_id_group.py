"""A post pattern with no id group cannot key anything on the link."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class NoIdProvider(Provider):
    name: ClassVar[str] = "No Id"
    hint: ClassVar[str] = "noid.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://noid\.example/\d+")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
