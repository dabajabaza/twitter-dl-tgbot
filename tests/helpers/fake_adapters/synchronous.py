"""A synchronous store would block the event loop: rejected at discovery."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class SynchronousDestination(OverflowDestination):
    label = "Synchronous"

    def store(self, source: Path, *, name: str) -> str:  # type: ignore[override]
        return name
