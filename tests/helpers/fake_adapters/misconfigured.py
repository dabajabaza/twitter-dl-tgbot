"""Construction fails the way missing env settings do: MISCONFIGURED."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class MisconfiguredDestination(OverflowDestination):
    label = "Broken"

    def __init__(self) -> None:
        raise ValueError("missing credentials")

    async def store(self, source: Path, *, name: str) -> str:
        return name
