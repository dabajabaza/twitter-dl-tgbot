"""A re.VERBOSE pattern. Idiomatic, and invisible to any scheme that splices pattern text."""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.providers import Provider, ProviderContext


class _Engine:
    async def download(self, url, dest, *, on_progress=None, max_bytes=None):
        return []


class VerbosePatternProvider(Provider):
    name: ClassVar[str] = "Verbose"
    hint: ClassVar[str] = "verbose.example/<id>"
    post_link: ClassVar[re.Pattern[str]] = re.compile(
        r"""
        https://verbose\.example/   # the host
        (?P<id>\d+)                 # the post id
        """,
        re.VERBOSE,
    )

    def __init__(self, context: ProviderContext) -> None:
        pass

    @property
    def downloader(self) -> Downloader:
        return _Engine()
