"""The module name 'none' collides with the Off pseudo-choice: reserved."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class ReservedNameDestination(OverflowDestination):
    label = "Reserved"

    async def store(self, source: Path, *, name: str) -> str:
        return name
