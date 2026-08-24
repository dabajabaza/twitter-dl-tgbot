"""A label of whitespace is no name at all."""

from pathlib import Path

from twitter_dl.services.overflow import OverflowDestination


class BlankLabelDestination(OverflowDestination):
    label = "   "

    async def store(self, source: Path, *, name: str) -> str:
        return name
