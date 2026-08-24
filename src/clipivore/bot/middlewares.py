"""Who the bot talks to at all.

Every download runs under the owner's X account, so the guest list is short,
static, and lives in the environment rather than in a database — there is no
sign-up flow to store (see docs/ARCHITECTURE.md D1).
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import TelegramObject, User

logger = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

# Beyond this many distinct strangers the denial log has served its purpose and
# is only consuming memory; a bot username being crawled produces exactly that.
_MAX_TRACKED_STRANGERS = 256


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Ignores group traffic: this is a personal bot and every reply is a DM."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        chat = data.get("event_chat")
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None
        return await handler(event, data)


class AuthMiddleware(BaseMiddleware):
    """Drops updates from anyone outside the whitelist, silently.

    Silence rather than a refusal: bot usernames get crawled, and an answer of
    any kind confirms the bot exists and is alive.
    """

    def __init__(self, allowed_ids: frozenset[int]) -> None:
        self._allowed_ids = allowed_ids
        self._denied_seen: set[int] = set()

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self._allowed_ids:
            self._log_denial(user)
            return None
        return await handler(event, data)

    def _log_denial(self, user: User | None) -> None:
        if user is None:
            logger.debug("update without a user rejected")
            return
        if user.id in self._denied_seen:
            # Repeat offenders drop to debug: one persistent stranger should not
            # bury the log the owner reads for real problems.
            logger.debug("stranger %s rejected again", user.id)
            return
        if len(self._denied_seen) >= _MAX_TRACKED_STRANGERS:
            self._denied_seen.clear()
        self._denied_seen.add(user.id)
        logger.warning("stranger rejected: id=%s username=%s", user.id, user.username)
