"""A named backreference: it means nothing once the group names are stripped away."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class BackreferenceProvider(Provider):
    name: ClassVar[str] = "Backreference"
    hint: ClassVar[str] = "backref.example/<id>-<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(
        r"https://backref\.example/(?P<id>\d+)-(?P=id)"
    )

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
