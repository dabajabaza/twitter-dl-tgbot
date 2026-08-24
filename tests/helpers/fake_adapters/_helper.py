"""Underscore modules are shared helpers: discovery must not open this one."""

from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class HiddenDestination(OverflowDestination):
    label = "Hidden"

    async def store(self, source: Path, *, name: str) -> str:
        return name
