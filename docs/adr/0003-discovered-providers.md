# Discover providers by scanning the providers package

A *Provider* is a platform the bot can download from. Until now there was
exactly one, X, spelled out in three unrelated places: the link regexes and host
allowlist in `services/links.py`, the extractor lock and error-marker tables in
`services/downloader.py`, and the word "X" in a dozen strings in `bot/texts.py`.
Adding Bluesky meant editing all three and hoping none of X's assumptions leaked
across.

Providers are now discovered the way Overflow Adapters are
([ADR 0002](0002-discovered-overflow-adapters.md)): `services/providers.py`
scans `clipivore/providers/` at startup, and every non-underscore module holding
exactly one concrete `Provider` subclass is a Provider, its file name the stable
id. A subdirectory is a visible misconfigured entry rather than a silent skip,
and a module that fails to import, construct or shape up is retained as
misconfigured instead of stopping the bot. Adding a platform is adding a file.

Three things differ from the Adapters, each for a reason:

**Constructed with a `ProviderContext`, not with no arguments.** An Adapter's
settings are wholly its own, so zero-arg construction costs nothing. A Provider
needs the single outbound hop this deployment uses, which the bot has already
parsed (ARCHITECTURE.md D6) and which no Provider should re-derive. Everything
else a Provider needs it still reads itself, from its own environment — X keeps
the bare, unprefixed `COOKIES_FILE` name because that line already exists in the
hand-managed env file on the server, and a tidier spelling would have silently
turned authenticated downloads off on the next deploy.

**Nothing is selected and nothing is persisted.** The Owner picks one Adapter for
the whole bot; there is no equivalent choice here, because the *link* picks the
Provider. Every ready Provider is active at once, which is also why the state
enum has no `MISSING`: nothing stored can point at a module that went away.

**Link patterns are class attributes, so a broken Provider still claims its
links.** A Provider whose construction failed can still be recognised by the
patterns declared on its class, and its links get an explicit "X is
misconfigured" verdict. Without that they would fall to the no-link answer, and
a platform the bot knows about would be indistinguishable from one it never
heard of. A module that failed to *import* has no readable patterns and cannot
do this — its links do fall through, and the startup log is the only place that
breakage shows.

The extractor lock survives the move and gets stricter: it is now per Provider
(`EngineProfile.allowed_extractors`), never a union across them. A union would
let a post on one platform redirect into another platform's extractor, which is
exactly the hole the lock exists to close (ARCHITECTURE.md D15, D18).

The shared yt-dlp engine stays one file (D12). A Provider hands it an
`EngineProfile` — extractors, error markers, description cleaner, profile-URL
builder — rather than reimplementing the plumbing, so a yt-dlp-shaped platform
costs a declaration. A platform yt-dlp cannot serve at all is still possible:
`Provider.downloader` returns whatever satisfies the `Downloader` protocol, and
nothing in the mechanism assumes an engine. Threads, which has no yt-dlp
extractor, is the case this leaves room for.
