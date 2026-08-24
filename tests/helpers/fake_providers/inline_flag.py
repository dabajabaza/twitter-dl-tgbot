"""An inline (?i) instead of a flag argument: legal on its own, fatal when spliced."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class InlineFlagProvider(Provider):
    name: ClassVar[str] = "Inline Flag"
    hint: ClassVar[str] = "inline.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"(?i)https://inline\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
