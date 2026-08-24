"""No IGNORECASE: the Provider means it, and nothing may widen the match on its behalf."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class CaseSensitiveProvider(Provider):
    name: ClassVar[str] = "Case Sensitive"
    hint: ClassVar[str] = "case.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://case\.example/(?P<id>[a-z]+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
