"""Discovery: a module is a Provider, and a broken one never takes the bot down.

The failure modes live in tests/helpers/fake_providers, one module each, so the
scanner is exercised against real imports rather than against a mock of itself.
"""

import re

import pytest

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
        assert [choice.provider_id for choice in catalog().choices if choice.ready] == ["good"]

    def test_underscore_modules_are_helpers_not_providers(self) -> None:
        assert catalog().get("_helper") is None

    def test_a_package_is_not_a_provider_but_is_almost_certainly_a_mistake(self) -> None:
        packaged = catalog().get("packaged")
        assert packaged is not None
        assert packaged.state is ProviderState.MISCONFIGURED
        assert "single module" in packaged.error

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
        assert [choice.name for choice in catalog().ready] == ["Good"]

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
