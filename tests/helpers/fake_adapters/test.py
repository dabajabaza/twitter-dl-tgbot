"""The one well-formed Adapter: discovered, constructed, READY."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class FakeDestination(OverflowDestination):
    label = "Test destination"

    async def store(self, source: Path, *, name: str) -> str:
        return f"https://example.test/{name}"
