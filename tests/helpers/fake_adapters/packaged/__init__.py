"""An Adapter is a single module: a package must be refused visibly.

The destination inside is perfectly valid on purpose — silence here would
reproduce the empty-Menu-with-no-hint problem ADR 0002 was written against.
"""

from pathlib import Path

from twitter_dl.services.overflow import OverflowDestination


class PackagedDestination(OverflowDestination):
    label = "Packaged"

    async def store(self, source: Path, *, name: str) -> str:
        return name
