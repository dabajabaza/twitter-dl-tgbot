"""Two concrete destinations in one module: no way to tell which is the Adapter."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class FirstDestination(OverflowDestination):
    label = "First"

    async def store(self, source: Path, *, name: str) -> str:
        return name


class SecondDestination(OverflowDestination):
    label = "Second"

    async def store(self, source: Path, *, name: str) -> str:
        return name
