"""A constructor demanding arguments breaks the zero-arg discovery contract."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class NeedsArgumentsDestination(OverflowDestination):
    label = "Needs arguments"

    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def store(self, source: Path, *, name: str) -> str:
        return name
