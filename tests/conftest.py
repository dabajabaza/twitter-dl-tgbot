"""Fixtures shared by the whole suite.

Lives at the top of ``tests/`` rather than in ``helpers/`` because pytest only
auto-loads conftest along the rootdir → test-file chain.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiogram import Bot

from clipivore.__main__ import build_dispatcher
from clipivore.bot.handlers import fallback, links, overflow, start
from clipivore.config import Settings
from clipivore.runtime.worker import RequestQueue
from clipivore.services.overflow import OverflowCatalog
from clipivore.services.providers import ProviderCatalog
from tests.helpers.bot_harness import BotHarness, RecordingSession
from tests.helpers.factories import build_settings

# Handler routers are module-level singletons and a Router may attach to only
# one Dispatcher per lifetime, so each test must hand them back. Kept in step
# with build_dispatcher's list by test_architecture.py.
_SHARED_ROUTERS = (start.router, overflow.router, links.router, fallback.router)

# Every environment variable a Provider reads for itself. `Settings` is built
# with `_env_file=None` in the factories, but a Provider constructs its own
# settings inside the catalog, where a test cannot reach in — so the ambient
# values have to go before it is built. A new Provider with settings of its own
# adds them here; forget to, and its tests quietly test the developer's machine.
_PROVIDER_ENV_VARS = ("COOKIES_FILE",)


@pytest.fixture(autouse=True)
def hermetic_provider_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's own environment out of every Provider.

    Without this, `COOKIES_FILE=/tmp` in a shell — or a production `.env` copied
    into the working tree — makes Twitter misconfigured and fails tests that have
    nothing to do with cookies. Anyone running the suite on the deploy host hits
    it immediately.
    """
    for name in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # pydantic-settings resolves `.env` against the working directory, so moving
    # out of the repository is what makes a stray one invisible.
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return build_settings(tmp_path)


@pytest.fixture
async def harness(settings: Settings) -> AsyncIterator[BotHarness]:
    session = RecordingSession()
    bot = Bot(token=settings.bot_token, session=session)
    queue = RequestQueue(settings.queue_limit)
    # An empty adapters package: tests that need adapters build their own
    # catalog from tests.helpers.fake_adapters and swap it into dp.
    overflow_catalog = OverflowCatalog(
        "tests.helpers.no_adapters",
        state_file=settings.overflow_state_file,
    )
    # The real providers package: handler tests need Twitter's actual link patterns,
    # and nothing here ever downloads.
    provider_catalog = ProviderCatalog()
    dp = build_dispatcher(settings, queue, overflow_catalog, provider_catalog)
    await dp.emit_startup()
    try:
        yield BotHarness(
            bot=bot,
            dp=dp,
            session=session,
            queue=queue,
            overflow_catalog=overflow_catalog,
            provider_catalog=provider_catalog,
        )
    finally:
        await dp.emit_shutdown()
        for router in _SHARED_ROUTERS:
            router._parent_router = None
