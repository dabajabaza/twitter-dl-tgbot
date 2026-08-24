"""Providers: the platforms this bot can download from, and how they are found.

A *Provider* is one platform — X, Bluesky. A module in the providers package IS
a Provider: the file name is its stable id, and its one concrete `Provider`
subclass is the implementation. That mirrors the Overflow Adapters exactly
(docs/adr/0002), for the same reason: adding one should be writing a file, not
editing a registry that then disagrees with the directory.

Three things differ from the Adapters, and docs/adr/0003 records why:

* a Provider is constructed with a `ProviderContext` rather than no arguments,
  because the transport it needs is already parsed and shared;
* nothing is selected and nothing is persisted — every ready Provider is active
  at once, and it is the *link* that picks one;
* the link patterns are declared on the class, so a Provider whose construction
  failed still claims its own links. That turns a broken Provider from a silence
  into a named refusal, which is the difference between "the bot ignored me" and
  "the owner has something to fix".
"""

import importlib
import inspect
import logging
import pkgutil
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from clipivore.domain import Downloader

logger = logging.getLogger(__name__)

PROVIDERS_PACKAGE = "clipivore.providers"
_PROVIDER_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")


@dataclass(frozen=True)
class ProviderContext:
    """Infrastructure the bot has already parsed, handed to every Provider.

    Deliberately not "the settings object": a Provider reads its own settings
    itself, from its own environment prefix. What it cannot sensibly re-derive
    is the single outbound hop this deployment uses (ARCHITECTURE.md D6), so
    that is passed in.
    """

    proxy: str | None = None


class Provider(ABC):
    """One platform the bot can download from.

    Everything needed to *recognise* a link is a class attribute, so it can be
    read off a Provider that never successfully constructed. Everything needed
    to *serve* a link is an instance concern.
    """

    #: What the bot's replies call this platform. "X", "Bluesky".
    name: ClassVar[str]
    #: One line for /help, naming the link shapes a person can send.
    hint: ClassVar[str]
    #: A URL naming a single post. Must carry a named group ``id``.
    post_link: ClassVar[re.Pattern[str]]
    #: A wrapper whose target is unknown until followed, if the platform has one.
    short_link: ClassVar[re.Pattern[str] | None] = None

    #: The owner's session with this platform, when it has one.
    cookies: object | None = None

    @abstractmethod
    def __init__(self, context: ProviderContext) -> None: ...

    @property
    @abstractmethod
    def downloader(self) -> Downloader:
        """The engine that fetches this platform's clips."""

    async def resolve(self, url: str) -> str:
        """Turn whatever arrived into a direct post link.

        A platform with no short link inherits this: a direct link resolves to
        itself, touching the network not at all.
        """
        return url

    @classmethod
    def post_id(cls, url: str) -> str | None:
        """The id the post is keyed on, from the link alone."""
        match = cls.post_link.match(url)
        return match["id"] if match else None

    @classmethod
    def claims(cls, url: str) -> bool:
        """Whether this Provider recognises ``url`` at all."""
        if cls.post_link.match(url):
            return True
        return bool(cls.short_link and cls.short_link.fullmatch(url))

    @classmethod
    def is_short(cls, url: str) -> bool:
        return bool(cls.short_link and cls.short_link.fullmatch(url))


class ProviderState(StrEnum):
    """Whether a discovered Provider can actually serve a link.

    There is no MISSING here, unlike the Adapters: nothing selects a Provider, so
    there is no stored choice left pointing at a module that went away.
    """

    READY = "ready"
    MISCONFIGURED = "misconfigured"


@dataclass(frozen=True)
class ProviderChoice:
    """One discovered Provider, ready or not.

    ``cls`` is kept even when construction failed — that is what lets a broken
    Provider keep claiming its links. ``provider`` is the built instance, and is
    None exactly when the state is not READY.
    """

    provider_id: str
    name: str
    state: ProviderState
    cls: type[Provider] | None = None
    provider: Provider | None = None
    error: str = ""

    @property
    def ready(self) -> bool:
        return self.state is ProviderState.READY

    def claims(self, url: str) -> bool:
        return bool(self.cls and self.cls.claims(url))


@dataclass(frozen=True)
class ClaimedLink:
    """One link found in a message, and the Provider that recognised it."""

    url: str
    choice: ProviderChoice = field(compare=False)


class ProviderCatalog:
    """Every Provider the package holds, and the link matching built from them.

    Built once at startup. The combined pattern is assembled here rather than
    per-Provider so that one pass over a message returns links in the order a
    person wrote them — across platforms as well as within one. Two passes
    reordered them, and with a nearly full queue that decided which of somebody's
    links got dropped.
    """

    def __init__(
        self,
        context: ProviderContext | None = None,
        *,
        package: str = PROVIDERS_PACKAGE,
    ) -> None:
        self._choices = _discover(package, context or ProviderContext())
        self._pattern = _combined_pattern(self._choices.values())
        for choice in self._choices.values():
            if not choice.ready:
                logger.error(
                    "provider %s is %s: %s", choice.provider_id, choice.state.value, choice.error
                )

    @property
    def choices(self) -> list[ProviderChoice]:
        """Every discovered Provider, in module-name order."""
        return list(self._choices.values())

    @property
    def ready(self) -> list[ProviderChoice]:
        return [choice for choice in self._choices.values() if choice.ready]

    def get(self, provider_id: str) -> ProviderChoice | None:
        return self._choices.get(provider_id)

    def claim(self, url: str) -> ProviderChoice | None:
        """Which Provider recognises ``url``, if any."""
        for choice in self._choices.values():
            if choice.claims(url):
                return choice
        return None

    def extract(self, *sources: str | None) -> list[ClaimedLink]:
        """Every recognised link across ``sources``, in order of first appearance.

        Duplicates are dropped, so forwarding a message that repeats the same
        link enqueues it once.
        """
        if self._pattern is None:
            return []
        found: list[ClaimedLink] = []
        seen: set[str] = set()
        for source in sources:
            if not source:
                continue
            for match in self._pattern.finditer(source):
                url = match.group(0).rstrip(".;:!?)")
                if url in seen:
                    continue
                choice = self.claim(url)
                if choice is None:
                    # The combined pattern matched a prefix that the trailing
                    # punctuation strip has since changed. Nothing claims it.
                    continue
                seen.add(url)
                found.append(ClaimedLink(url=url, choice=choice))
        return found


def _combined_pattern(choices: Iterable[ProviderChoice]) -> re.Pattern[str] | None:
    alternatives: list[str] = []
    for choice in choices:
        if choice.cls is None:
            continue
        alternatives.append(f"(?:{choice.cls.post_link.pattern})")
        if choice.cls.short_link is not None:
            alternatives.append(f"(?:{choice.cls.short_link.pattern})")
    if not alternatives:
        return None
    # The named `id` group appears once per Provider, which one pattern cannot
    # have twice — the names are stripped, since matching here only needs the
    # span. Each Provider re-parses its own id from the URL afterwards.
    return re.compile("|".join(_strip_group_names(part) for part in alternatives), re.IGNORECASE)


def _strip_group_names(pattern: str) -> str:
    return re.sub(r"\(\?P<[^>]+>", "(?:", pattern)


def _discover(package: str, context: ProviderContext) -> dict[str, ProviderChoice]:
    """Every Provider the providers package holds, in module-name order.

    Modules whose names start with an underscore are shared helpers. A
    subdirectory is not a Provider either — but unlike a helper it is almost
    certainly a mistake, so it shows up as misconfigured rather than as nothing.
    """
    location = importlib.import_module(package)
    entries = sorted(pkgutil.iter_modules(location.__path__), key=lambda info: info.name)
    choices: dict[str, ProviderChoice] = {}
    for info in entries:
        if info.name.startswith("_"):
            continue
        if info.ispkg:
            choices[info.name] = _broken(
                info.name,
                _name_from_id(info.name),
                "a Provider is a single module, not a package",
            )
            continue
        choices[info.name] = _load(info.name, f"{package}.{info.name}", context)
    return choices


def _load(provider_id: str, module_name: str, context: ProviderContext) -> ProviderChoice:
    name = _name_from_id(provider_id)
    if not _PROVIDER_ID.fullmatch(provider_id):
        return _broken(provider_id, name, "provider module name must match [a-z][a-z0-9_-]{0,47}")

    try:
        module = importlib.import_module(module_name)
    except (Exception, SystemExit) as exc:
        # The scanner just saw the file, so a failed import is a broken
        # Provider. Its patterns are unknowable, so its links will fall through
        # to the no-link answer — the startup log is the only place this shows.
        return _broken(provider_id, name, str(exc))

    classes = [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, Provider)
        and value.__module__ == module.__name__
        and not inspect.isabstract(value)
    ]
    if not classes:
        return _broken(provider_id, name, "module defines no concrete Provider subclass")
    if len(classes) > 1:
        names = ", ".join(sorted(cls.__name__ for cls in classes))
        return _broken(
            provider_id, name, f"module defines several Provider subclasses ({names}); expected one"
        )
    cls = classes[0]

    invalid = _declaration_error(cls)
    if invalid is not None:
        # No `cls` on the choice: a class whose patterns are unusable cannot be
        # trusted to claim links with them.
        return _broken(provider_id, name, invalid)
    # From here on even a broken Provider is reported under its own name, and
    # keeps claiming its links.
    name = cls.name

    try:
        provider = cls(context)
        downloader = provider.downloader
        if not inspect.iscoroutinefunction(provider.resolve):
            raise TypeError(f"{cls.__name__}.resolve must be async")
        if not inspect.iscoroutinefunction(getattr(downloader, "download", None)):
            raise TypeError(f"{cls.__name__}.downloader must provide an async download()")
    except (Exception, SystemExit) as exc:
        return _broken(provider_id, name, str(exc), cls=cls)

    return ProviderChoice(
        provider_id=provider_id,
        name=name,
        state=ProviderState.READY,
        cls=cls,
        provider=provider,
    )


def _declaration_error(cls: type[Provider]) -> str | None:
    """Whether the class-level declarations are usable, before anything is built."""
    for attribute in ("name", "hint"):
        value = getattr(cls, attribute, None)
        if not isinstance(value, str) or not value.strip():
            return f"{cls.__name__}.{attribute} must be a non-empty string class attribute"
    pattern = getattr(cls, "post_link", None)
    if not isinstance(pattern, re.Pattern):
        return f"{cls.__name__}.post_link must be a compiled regular expression"
    if "id" not in pattern.groupindex:
        return f"{cls.__name__}.post_link must carry a named group 'id'"
    short = getattr(cls, "short_link", None)
    if short is not None and not isinstance(short, re.Pattern):
        return f"{cls.__name__}.short_link must be a compiled regular expression or None"
    return None


def _broken(
    provider_id: str, name: str, error: str, *, cls: type[Provider] | None = None
) -> ProviderChoice:
    return ProviderChoice(
        provider_id=provider_id,
        name=name,
        state=ProviderState.MISCONFIGURED,
        cls=cls,
        error=error,
    )


def _name_from_id(provider_id: str) -> str:
    return provider_id.replace("_", " ").replace("-", " ").title()
