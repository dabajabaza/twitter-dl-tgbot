"""FSM storage for a bot that has no dialogs.

aiogram resolves an FSM context for every update, and it does so in a
middleware registered inside ``Dispatcher.__init__`` — which means it runs
*before* the access gates this bot adds afterwards. With the default
``MemoryStorage`` (a defaultdict) that lookup creates a record keyed by the
sender, so anyone who messages the bot leaves a trace behind, whitelisted or
not: unbounded growth driven by people who are not allowed to use it at all.

There is nothing to store anyway. This bot has no states and no multi-step
dialogs — a link is the whole conversation (see docs/ARCHITECTURE.md D9). So
the storage keeps nothing and reports nothing, and the middleware ordering
stops mattering.
"""

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey


class NoStorage(BaseStorage):
    """Accepts every write, remembers none of it, and answers "no state"."""

    async def set_state(self, key: StorageKey, state: State | str | None = None) -> None:
        return None

    async def get_state(self, key: StorageKey) -> str | None:
        return None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        return None

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return {}

    async def close(self) -> None:
        return None
