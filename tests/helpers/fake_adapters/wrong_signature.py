"""A store that cannot take (source, *, name) is rejected at discovery."""

from twitter_dl.services.overflow import OverflowDestination


class WrongSignatureDestination(OverflowDestination):
    label = "Wrong signature"

    async def store(self) -> str:  # type: ignore[override]
        return "unused"
