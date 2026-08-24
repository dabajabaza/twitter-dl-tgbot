"""A Provider whose engine cannot download is no Provider at all."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class EnginelessProvider(Provider):
    name: ClassVar[str] = "Engineless"
    hint: ClassVar[str] = "engineless.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(r"https://engineless\.example/(?P<id>\d+)")

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return object()  # type: ignore[return-value]
