"""Importable Adapter factories used to exercise configured loading."""

from pathlib import Path

from twitter_dl.services.overflow import OverflowDestination


class FakeDestination(OverflowDestination):
    label = "Test destination"

    async def store(self, source: Path, *, name: str) -> str:
        return f"https://example.test/{name}"


def create() -> FakeDestination:
    return FakeDestination()


def misconfigured() -> FakeDestination:
    raise ValueError("missing credentials")


class DuckDestination:
    label = "Looks compatible"

    async def store(self, source: Path, *, name: str) -> str:
        return name


def duck() -> DuckDestination:
    return DuckDestination()


class SynchronousDestination(OverflowDestination):
    label = "Synchronous"

    def store(self, source: Path, *, name: str) -> str:  # type: ignore[override]
        return name


def synchronous() -> SynchronousDestination:
    return SynchronousDestination()


class WrongSignatureDestination(OverflowDestination):
    label = "Wrong signature"

    async def store(self) -> str:  # type: ignore[override]
        return "unused"


def wrong_signature() -> WrongSignatureDestination:
    return WrongSignatureDestination()


class StatefulLabelDestination(OverflowDestination):
    def __init__(self) -> None:
        self._reads = 0

    @property
    def label(self) -> str:
        self._reads += 1
        if self._reads > 1:
            raise RuntimeError("label read twice")
        return "Read once"

    async def store(self, source: Path, *, name: str) -> str:
        return name


def stateful_label() -> StatefulLabelDestination:
    return StatefulLabelDestination()


def __getattr__(name: str) -> object:
    if name == "exploding_attribute":
        raise RuntimeError("module attribute lookup failed")
    raise AttributeError(name)
