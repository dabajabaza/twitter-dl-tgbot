"""resolve() must be awaitable: the worker awaits it."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class SyncResolveProvider(Provider):
    name: ClassVar[str] = "Sync Resolve"
    hint: ClassVar[str] = "sync.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://sync\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()

    def resolve(self, url):  # noqa: ANN001, ANN201 — deliberately not async
        return url
