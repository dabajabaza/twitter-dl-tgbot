"""Menu names come from the class: an instance-level label is rejected."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class PropertyLabelDestination(OverflowDestination):
    @property
    def label(self) -> str:  # type: ignore[override]
        return "Computed at runtime"

    async def store(self, source: Path, *, name: str) -> str:
        return name
