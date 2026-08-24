"""post_link must be a compiled pattern, not a string that looks like one."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class StringPatternProvider(Provider):
    name: ClassVar[str] = "String Pattern"
    hint: ClassVar[str] = "string.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = "https://string.example/(?P<id>d+)"  # type: ignore[assignment]

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
