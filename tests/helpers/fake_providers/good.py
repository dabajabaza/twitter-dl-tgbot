"""A Provider that works, so the failures have a control to sit beside."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class GoodProvider(Provider):
    name: ClassVar[str] = "Good"
    hint: ClassVar[str] = "good.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://good\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        self.context = context

    @property
    def downloader(self) -> Downloader:
        return _Engine()
