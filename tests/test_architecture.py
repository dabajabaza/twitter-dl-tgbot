"""Rules about the shape of the code that no unit test would notice breaking."""

from pathlib import Path

import twitter_dl
from twitter_dl.__main__ import build_dispatcher
from twitter_dl.bot.handlers import fallback

SRC = Path(twitter_dl.__file__).parent


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(SRC)): path.read_text(encoding="utf-8") for path in SRC.rglob("*.py")
    }


def test_only_the_download_engine_knows_about_yt_dlp() -> None:
    # The rule that keeps the engine replaceable and the worker testable: every
    # other module speaks in `domain.Clip`, so swapping yt-dlp out is one file.
    importers = {name for name, source in _sources().items() if "yt_dlp" in source}
    assert importers == {"services/downloader.py"}


def test_only_the_presentation_layer_knows_about_aiogram() -> None:
    # services/ and domain.py must stay free of the chat framework; delivery is
    # the deliberate exception, since sending the video IS its job.
    allowed = {"__main__.py", "domain.py", "config.py", "errors.py"}
    for name, source in _sources().items():
        if name.startswith(("bot/", "runtime/")) or name in allowed:
            continue
        if name == "services/delivery.py":
            continue
        assert "aiogram" not in source, name


def test_the_catch_all_router_is_registered_last(
    settings,
) -> None:  # noqa: ANN001
    # fallback matches any message, so anything registered after it would be
    # dead code — and the failure would be silent.
    from twitter_dl.runtime.worker import RequestQueue

    dp = build_dispatcher(settings, RequestQueue(settings.queue_limit))
    try:
        assert dp.sub_routers[-1] is fallback.router
    finally:
        for router in dp.sub_routers:
            router._parent_router = None


def test_the_test_harness_hands_back_every_router_production_uses() -> None:
    # tests/conftest.py resets these between tests; a router missed there
    # attaches to a second Dispatcher and every later test dies on setup.
    from tests.conftest import _SHARED_ROUTERS
    from twitter_dl.bot.handlers import links, start

    assert set(_SHARED_ROUTERS) == {start.router, links.router, fallback.router}
