"""Rules about the shape of the code that no unit test would notice breaking.

Checked by parsing imports rather than grepping text: a docstring explaining
*why* a module avoids yt-dlp must not read as a violation of that very rule.
"""

import ast
from pathlib import Path

import twitter_dl
from tests.helpers.factories import build_settings
from twitter_dl.__main__ import build_dispatcher
from twitter_dl.bot.handlers import fallback, links, overflow, start
from twitter_dl.runtime.worker import RequestQueue
from twitter_dl.services.overflow import OverflowCatalog

SRC = Path(twitter_dl.__file__).parent


def _modules() -> dict[str, ast.Module]:
    return {
        str(path.relative_to(SRC)): ast.parse(path.read_text(encoding="utf-8"))
        for path in SRC.rglob("*.py")
    }


def _imports(tree: ast.Module) -> set[str]:
    """Every top-level package this module imports, however it spells it."""
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            packages.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            packages.add(node.module.split(".")[0])
    return packages


def _importers_of(package: str) -> set[str]:
    return {name for name, tree in _modules().items() if package in _imports(tree)}


def test_only_the_download_engine_knows_about_yt_dlp() -> None:
    # The rule that keeps the engine replaceable and the worker testable: every
    # other module speaks in `domain.Clip`, so swapping yt-dlp out is one file.
    assert _importers_of("yt_dlp") == {"services/downloader.py"}


def test_only_the_presentation_layer_knows_about_aiogram() -> None:
    # domain.py and the pure services must stay free of the chat framework, so
    # they can be exercised without one. delivery.py is the deliberate
    # exception: sending the video IS its job.
    allowed = {
        "__main__.py",
        "services/delivery.py",
        "runtime/watchdog.py",
        "runtime/worker.py",
    }
    offenders = {
        name
        for name in _importers_of("aiogram")
        if not name.startswith("bot/") and name not in allowed
    }
    assert offenders == set()


def test_the_domain_vocabulary_depends_on_neither_framework() -> None:
    # What lets the worker and the delivery route be built and tested without a
    # download engine (ARCHITECTURE.md D12).
    imports = _imports(_modules()["domain.py"])
    assert "aiogram" not in imports
    assert "yt_dlp" not in imports


def test_the_cookie_store_never_reaches_for_a_framework() -> None:
    # It exists to keep the owner's export away from yt-dlp; importing yt-dlp
    # here would defeat the point of having the seam at all.
    assert "yt_dlp" not in _imports(_modules()["services/cookies.py"])


def test_the_catch_all_router_is_registered_last(tmp_path: Path) -> None:
    # fallback matches any message, so anything registered after it would be
    # dead code — and the failure would be silent.
    settings = build_settings(tmp_path)
    catalog = OverflowCatalog({}, default="none", state_file=settings.overflow_state_file)
    dp = build_dispatcher(settings, RequestQueue(settings.queue_limit), catalog)
    try:
        assert dp.sub_routers[-1] is fallback.router
    finally:
        for router in dp.sub_routers:
            router._parent_router = None


def test_the_test_harness_hands_back_every_router_production_uses() -> None:
    # tests/conftest.py resets these between tests; a router missed there
    # attaches to a second Dispatcher and every later test dies on setup.
    from tests.conftest import _SHARED_ROUTERS

    assert set(_SHARED_ROUTERS) == {
        start.router,
        overflow.router,
        links.router,
        fallback.router,
    }
