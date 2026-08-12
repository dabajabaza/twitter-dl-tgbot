"""Driving a real dispatcher through fabricated updates, without a network."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage, SendVideo, TelegramMethod
from aiogram.methods.get_me import GetMe
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from twitter_dl.runtime.worker import RequestQueue

M = TypeVar("M", bound=TelegramMethod[Any])


class RecordingSession(BaseSession):
    """aiogram session double: records every outgoing API call and returns
    fabricated results shaped to satisfy aiogram's own response validation.

    Inherits BaseSession rather than duck-typing it, so ``Bot.session`` accepts
    it and ``Bot.__call__`` works unmodified. ``fail_on`` programs a failure by
    method class name.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.fail_on: dict[str, Exception] = {}
        self._next_message_id = 5000

    def _next_message(self, chat_id: int, text: str | None) -> Message:
        self._next_message_id += 1
        return Message(
            message_id=self._next_message_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name in self.fail_on:
            raise self.fail_on[name]

        if isinstance(method, SendMessage | EditMessageText | SendVideo):
            assert isinstance(method.chat_id, int)
            return self._next_message(method.chat_id, getattr(method, "text", None))
        if isinstance(method, GetMe):
            return TgUser(id=1, is_bot=True, first_name="Bot", username="testbot")
        return True

    async def close(self) -> None:
        pass

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    def calls_of(self, method_type: type[M]) -> list[M]:
        """Every recorded call of one method, typed as that method.

        Keyed by class rather than by name so a test reaching for `.caption`
        is checked against the real Bot API signature.
        """
        return [method for method in self.calls if isinstance(method, method_type)]

    def sent_texts(self) -> list[str]:
        return [
            text for method in self.calls if (text := getattr(method, "text", None)) is not None
        ]

    def clear(self) -> None:
        self.calls.clear()


def make_update_message(
    text: str,
    *,
    user_id: int,
    chat_id: int | None = None,
    chat_type: str = "private",
    update_id: int = 1,
) -> Update:
    chat = Chat(id=chat_id if chat_id is not None else user_id, type=chat_type)
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = Message(
        message_id=update_id, date=datetime.now(UTC), chat=chat, from_user=user, text=text
    )
    return Update(update_id=update_id, message=message)


@dataclass
class BotHarness:
    """A real Dispatcher — the one ``build_dispatcher`` wires for production —
    fed fabricated updates, with everything it tried to send recorded."""

    bot: Bot
    dp: Dispatcher
    session: RecordingSession
    queue: RequestQueue
    _next_update_id: int = field(default=1)

    def _update_id(self) -> int:
        self._next_update_id += 1
        return self._next_update_id

    async def send(self, text: str, *, user_id: int = 1, chat_type: str = "private") -> None:
        update = make_update_message(
            text, user_id=user_id, chat_type=chat_type, update_id=self._update_id()
        )
        await self.dp.feed_update(self.bot, update)
