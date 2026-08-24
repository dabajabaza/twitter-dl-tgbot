"""Discovery: a module is a Provider, and a broken one never takes the bot down.

The failure modes live in tests/helpers/fake_providers, one module each, so the
scanner is exercised against real imports rather than against a mock of itself.
"""

import re

import pytest

from clipivore.__main__ import _require_providers
from clipivore.services.providers import (
    Provider,
    ProviderCatalog,
    ProviderContext,
    ProviderState,
)
from tests.helpers.fake_providers.good import GoodProvider

FAKES = "tests.helpers.fake_providers"


def catalog(package: str = FAKES, proxy: str | None = None) -> ProviderCatalog:
    return ProviderCatalog(ProviderContext(proxy=proxy), package=package)


class TestWhatCountsAsAProvider:
    def test_a_working_module_becomes_a_ready_provider(self) -> None:
        good = catalog().get("good")
        assert good is not None
        assert good.state is ProviderState.READY
        assert good.name == "Good"
        assert good.provider is not None

    def test_the_file_name_is_the_stable_id(self) -> None:
        # The class may be renamed freely; the id a log line quotes may not.
        # GoodProvider lives in good.py, so its id is "good" and not "Good".
        good = catalog().get("good")
        assert good is not None
        assert good.cls is GoodProvider
        assert catalog().get("GoodProvider") is None

    def test_underscore_modules_are_helpers_not_providers(self) -> None:
        assert catalog().get("_helper") is None

    def test_a_package_is_not_a_provider_but_is_almost_certainly_a_mistake(self) -> None:
        packaged = catalog().get("packaged")
        assert packaged is not None
        assert packaged.state is ProviderState.MISCONFIGURED
        assert "single module" in packaged.error

    def test_a_bare_directory_is_reported_too_not_silently_skipped(self) -> None:
        # The likelier mistake of the two: a folder started for a Provider and
        # never given an __init__.py. The module scanner does not report it at
        # all, so without a directory walk of its own it is not a broken
        # Provider — it is nothing, with not one line in the log to say so.
        stray = catalog().get("stray_directory")
        assert stray is not None
        assert stray.state is ProviderState.MISCONFIGURED
        assert "not a directory" in stray.error

    def test_the_pycache_the_test_run_itself_leaves_behind_is_not_a_provider(self) -> None:
        # Underscore-prefixed directories are skipped, which is what keeps
        # __pycache__ from being announced as somebody's broken Provider.
        assert catalog().get("__pycache__") is None

    def test_a_lookalike_that_subclasses_nothing_is_not_discovered(self) -> None:
        duck = catalog().get("duck")
        assert duck is not None
        assert duck.state is ProviderState.MISCONFIGURED

    def test_an_empty_package_leaves_the_bot_running_with_no_providers(self) -> None:
        empty = catalog("tests.helpers.no_providers")
        assert empty.choices == []
        assert empty.extract("https://x.com/a/status/1") == []

    def test_the_real_package_holds_at_least_x(self) -> None:
        names = {choice.name for choice in ProviderCatalog().ready}
        assert "X" in names


class TestBrokenProvidersAreVisibleNotFatal:
    @pytest.mark.parametrize(
        ("provider_id", "fragment"),
        [
            ("crashing", "cannot be imported"),
            ("exiting", "exit"),
            ("misconfigured", "no credentials"),
            ("ambiguous", "several Provider subclasses"),
            ("blank_name", "name must be a non-empty string"),
            ("no_id_group", "named group 'id'"),
            ("unpatterned", "compiled regular expression"),
            ("synchronous", "resolve must be async"),
            ("engineless", "async download()"),
        ],
    )
    def test_every_way_a_provider_can_be_wrong_is_reported_not_raised(
        self, provider_id: str, fragment: str
    ) -> None:
        choice = catalog().get(provider_id)
        assert choice is not None
        assert choice.state is ProviderState.MISCONFIGURED
        assert fragment in choice.error
        assert choice.provider is None

    def test_one_broken_provider_does_not_hide_the_working_ones(self) -> None:
        built = catalog()
        broken = {choice.provider_id for choice in built.choices if not choice.ready}
        ready = {choice.provider_id for choice in built.ready}
        # Nine ways to be broken sit in this package alongside the working ones,
        # and neither set leaks into the other.
        assert "good" in ready
        assert broken and not (broken & ready)

    def test_a_provider_that_built_badly_still_claims_its_links(self) -> None:
        # The whole point of declaring patterns on the class: the person gets
        # "X is misconfigured" instead of the silence an unknown link earns.
        links = catalog().extract("https://broken.example/12")
        assert [link.choice.provider_id for link in links] == ["misconfigured"]
        assert not links[0].choice.ready

    def test_a_provider_that_never_imported_cannot_claim_anything(self) -> None:
        # Its patterns died with the import. The startup log is the only place
        # this breakage shows, which is why _log_providers exists.
        crashing = catalog().get("crashing")
        assert crashing is not None
        assert crashing.cls is None
        assert not crashing.claims("https://crashing.example/1")

    def test_a_class_with_unusable_patterns_is_not_trusted_to_claim_links(self) -> None:
        blank = catalog().get("blank_name")
        assert blank is not None
        assert blank.cls is None


class TestABotWithNoProvidersRefusesToRun:
    """Degrading is for "some of them broke", not for "all of them did"."""

    def test_an_empty_catalog_is_fatal_rather_than_quietly_healthy(self) -> None:
        # Without this the process reports READY=1, the watchdog stays green and
        # the deploy health check passes, while every link gets a refusal. D16
        # refuses that shape for the worker; it is no better here.
        with pytest.raises(SystemExit):
            _require_providers(catalog("tests.helpers.no_providers"))

    def test_one_working_provider_among_broken_ones_is_enough_to_run(self) -> None:
        _require_providers(catalog())


class TestAProviderKeepsItsOwnRegexSemantics:
    """The catalog must never reinterpret a Provider's pattern.

    An earlier version spliced every pattern's *text* into one alternation. That
    threw away the flags each pattern was compiled with and renumbered its
    groups, so ordinary regexes either matched nothing or refused to compile —
    and a refusal to compile happened inside the catalog's constructor, taking
    the whole bot down. These are the shapes that broke it.
    """

    @pytest.mark.parametrize(
        ("url", "provider_id"),
        [
            ("https://verbose.example/12", "verbose_pattern"),
            ("https://inline.example/34", "inline_flag"),
            ("https://backref.example/7-7", "backreference"),
            ("https://case.example/abc", "case_sensitive"),
        ],
    )
    def test_patterns_that_once_broke_the_catalog_now_match_normally(
        self, url: str, provider_id: str
    ) -> None:
        links = catalog().extract(f"look {url} here")
        assert [(link.url, link.choice.provider_id) for link in links] == [(url, provider_id)]

    def test_every_one_of_them_is_ready_rather_than_fatal(self) -> None:
        # The failure this replaced was not a wrong answer, it was no bot at all.
        for provider_id in ("verbose_pattern", "inline_flag", "backreference", "case_sensitive"):
            choice = catalog().get(provider_id)
            assert choice is not None
            assert choice.state is ProviderState.READY

    def test_a_case_sensitive_provider_is_not_widened_on_its_behalf(self) -> None:
        # It compiled without IGNORECASE deliberately; nothing may add it.
        assert catalog().extract("https://case.example/ABC") == []
        assert catalog().claim("https://case.example/ABC") is None

    def test_a_backreference_still_has_to_hold(self) -> None:
        assert catalog().extract("https://backref.example/7-8") == []


class TestOverlappingProvidersResolveByLongestMatch:
    URL = "https://good.example/123/extra"

    def test_the_provider_that_spells_out_more_of_the_url_wins(self) -> None:
        # Alphabetical order would hand this to `good` and silently truncate the
        # link to https://good.example/123 — a request for the wrong post.
        links = catalog().extract(self.URL)
        assert [(link.url, link.choice.provider_id) for link in links] == [
            (self.URL, "overlapping")
        ]

    def test_claim_and_extract_never_disagree_about_who_owns_a_link(self) -> None:
        claimed = catalog().claim(self.URL)
        assert claimed is not None
        assert claimed.provider_id == catalog().extract(self.URL)[0].choice.provider_id

    def test_the_narrower_provider_still_gets_the_urls_that_are_only_its(self) -> None:
        links = catalog().extract("https://good.example/123")
        assert [link.choice.provider_id for link in links] == ["good"]


class TestFindingLinksAcrossProviders:
    def test_links_come_back_in_the_order_a_person_wrote_them(self) -> None:
        # Across platforms too, not just within one: a per-Provider pass would
        # group them by platform, and with a nearly full queue that decides
        # whose link gets dropped.
        good = "https://good.example/2"
        broken = "https://broken.example/1"
        links = catalog().extract(f"{broken} then {good}")
        assert [link.url for link in links] == [broken, good]

    def test_the_same_link_twice_is_one_link(self) -> None:
        url = "https://good.example/7"
        assert [link.url for link in catalog().extract(f"{url} {url}")] == [url]

    def test_links_are_gathered_from_every_source_offered(self) -> None:
        first, second = "https://good.example/1", "https://good.example/2"
        assert [link.url for link in catalog().extract(None, first, second)] == [first, second]

    def test_a_link_nothing_claims_is_not_a_link(self) -> None:
        assert catalog().extract("https://youtube.com/watch?v=abc") == []

    def test_each_link_carries_the_provider_that_claimed_it(self) -> None:
        links = catalog().extract("https://good.example/3")
        assert links[0].choice.name == "Good"


class TestTheContextReachesTheProvider:
    def test_the_shared_proxy_is_handed_to_every_provider(self) -> None:
        # A Provider must not re-read the environment for what the bot already
        # parsed — there is one outbound hop, and it is decided centrally.
        good = catalog(proxy="http://127.0.0.1:1080").get("good")
        assert good is not None
        assert isinstance(good.provider, GoodProvider)
        assert good.provider.context.proxy == "http://127.0.0.1:1080"


class TestTheBaseClassCarriesTheDefaults:
    def test_a_provider_without_a_short_link_resolves_to_itself(self) -> None:
        class Direct(Provider):
            name = "Direct"
            hint = "direct.example/<id>"
            post_link = re.compile(r"https://direct\.example/(?P<id>\d+)")

            def __init__(self, context: ProviderContext) -> None:
                pass

            @property
            def downloader(self) -> object:  # type: ignore[override]
                return object()

        assert not Direct.is_short("https://direct.example/1")
        assert Direct.post_id("https://direct.example/1") == "1"
        assert Direct.post_id("https://elsewhere.example/1") is None
