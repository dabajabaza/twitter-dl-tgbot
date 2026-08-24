"""Right shape, wrong lineage: without the base class there is no Adapter here."""

from pathlib import Path


class DuckDestination:
    label = "Looks compatible"

    async def store(self, source: Path, *, name: str) -> str:
        return name
