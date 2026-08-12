"""Fixtures shared by the whole suite.

Lives at the top of ``tests/`` rather than in ``helpers/`` because pytest only
auto-loads conftest along the rootdir → test-file chain.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiogram import Bot

from tests.helpers.bot_harness import BotHarness, RecordingSession
from tests.helpers.factories import build_settings
from twitter_dl.__main__ import build_dispatcher
from twitter_dl.bot.handlers import fallback, links, start
from twitter_dl.config import Settings
from twitter_dl.runtime.worker import RequestQueue

# Handler routers are module-level singletons and a Router may attach to only
# one Dispatcher per lifetime, so each test must hand them back. Kept in step
# with build_dispatcher's list by test_architecture.py.
_SHARED_ROUTERS = (start.router, links.router, fallback.router)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return build_settings(tmp_path)


@pytest.fixture
async def harness(settings: Settings) -> AsyncIterator[BotHarness]:
    session = RecordingSession()
    bot = Bot(token=settings.bot_token, session=session)
    queue = RequestQueue(settings.queue_limit)
    dp = build_dispatcher(settings, queue)
    await dp.emit_startup()
    try:
        yield BotHarness(bot=bot, dp=dp, session=session, queue=queue)
    finally:
        await dp.emit_shutdown()
        for router in _SHARED_ROUTERS:
            router._parent_router = None
