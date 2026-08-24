"""X, the platform this bot was built for.

Two link shapes arrive in practice: the direct one
(``x.com/<user>/status/<id>``, plus the twitter.com and mobile. spellings that
still circulate) and the wrapped one (``t.co/<slug>``, which the X apps produce
and which says nothing about its target until followed).
"""

import re
from pathlib import Path
from typing import ClassVar

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from clipivore.domain import Downloader
from clipivore.services.cookies import CookieSession
from clipivore.services.downloader import EngineProfile, YtDlpDownloader
from clipivore.services.providers import Provider, ProviderContext
from clipivore.services.redirects import follow

# The username part is deliberately loose (X caps handles at 15 chars, but
# `i/web` and `i/status` are also valid prefixes) while the id is strictly
# numeric — that is the part we key on. A trailing photo/video segment, query
# string or fragment is common in shared links and kept.
#
# The tail excludes commas as well as whitespace: "…/status/111,https://x.com/…"
# is how two links arrive glued together in forwarded text, and a greedy tail
# swallowed the second one into the first, silently losing a request.
_POST_LINK = re.compile(
    r"https?://(?:www\.|mobile\.|m\.)?(?:twitter|x)\.com/"
    r"(?:i/web/|i/)?[A-Za-z0-9_]{1,20}/status(?:es)?/(?P<id>\d+)"
    r"[^\s<>\"',]*",
    re.IGNORECASE,
)
_SHORT_LINK = re.compile(r"https?://t\.co/[A-Za-z0-9]+", re.IGNORECASE)

# Every hop of a t.co redirect must land on one of these. Since the only thing
# worth following is a post on X, anything else is refused *before* the request
# is made (see services/redirects.py for why that ordering matters).
_ALLOWED_HOSTS = frozenset(
    {
        "t.co",
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "m.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "m.twitter.com",
    }
)

# X appends a t.co pointer to the post's own media at the end of the text; in a
# caption that link only duplicates the one the footer already carries. Both
# schemes occur in the wild. Only a *trailing* run is stripped — a t.co in the
# middle of a sentence is part of what the author said.
_TRAILING_TCO = re.compile(r"(?:\s*https?://t\.co/[A-Za-z0-9]+)+\s*$")


class XSettings(BaseSettings):
    """X's own configuration.

    ``COOKIES_FILE`` keeps its bare, unprefixed name on purpose: it is already
    set in the deployed env file, which is hand-managed on a host this
    repository cannot reach. A prefixed spelling would have been tidier and
    would have silently turned off authenticated downloads on the next deploy.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    cookies_file: Path | None = Field(
        default=None,
        alias="COOKIES_FILE",
        description=(
            "Netscape-format cookies.txt of the owner's X session. Without it only public "
            "posts download: no NSFW, no age-gated, no protected accounts"
        ),
    )

    @field_validator("cookies_file", mode="before")
    @classmethod
    def _empty_path_means_unset(cls, value: object) -> object:
        """An empty value is "not configured", not the current directory.

        `.env.example` invites `NAME=` for optional settings, and pydantic turns
        an empty string into `Path('.')` for a `Path | None` field. That start
        succeeds and then fails on every single download, with yt-dlp trying to
        read cookies out of a directory.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value


# What the shared engine needs to know about X. A description of the platform,
# not of this installation — so it is built once here rather than per instance,
# and reading it costs no configuration.
PROFILE = EngineProfile(
    allowed_extractors=("twitter.*",),
    account_state_markers=(
        "protected",
        "suspended",
        "deleted",
        "no longer exists",
        "does not exist",
        "doesn't exist",
        "not found",
    ),
    auth_markers=(
        "nsfw",
        "requires authentication",
        "only available for registered users",
        "log in",
        "login",
        "sign in",
        "logged in",
        "authoriz",
        "authenticat",
        "cookies",
        "account is required",
        "age-restricted",
        "age restricted",
    ),
    no_video_markers=(
        "no video could be found",
        "no video",
        "is not a video",
        "no media",
    ),
    unavailable_markers=("unavailable", "private"),
    clean_description=lambda text: _TRAILING_TCO.sub("", text),
    profile_url=lambda handle: f"https://x.com/{handle}",
)


class XProvider(Provider):
    name: ClassVar[str] = "X"
    hint: ClassVar[str] = "x.com/<user>/status/<id> — t.co short links work too"
    post_link: ClassVar[re.Pattern[str]] = _POST_LINK
    short_link: ClassVar[re.Pattern[str] | None] = _SHORT_LINK

    def __init__(self, context: ProviderContext) -> None:
        settings = XSettings()
        cookies_file = settings.cookies_file
        if cookies_file is not None and cookies_file.is_dir():
            # yt-dlp would try to read cookies out of a directory on every
            # download. Raising here makes X misconfigured rather than killing
            # the bot: it is named in /help and in the refusal, and any other
            # Provider keeps working. If X is the only one installed, though,
            # "every Provider is broken" is still fatal at startup — see D18.
            raise ValueError(f"COOKIES_FILE must be a file, got directory {cookies_file}")
        self.cookies = CookieSession(cookies_file)
        self._proxy = context.proxy
        self._downloader = YtDlpDownloader(PROFILE, cookies=self.cookies, proxy=context.proxy)

    @property
    def downloader(self) -> Downloader:
        return self._downloader

    async def resolve(self, url: str) -> str:
        """Follow a t.co wrapper to the post it points at."""
        if not self.is_short(url):
            return url
        return await follow(
            url,
            allowed_hosts=_ALLOWED_HOSTS,
            is_target=lambda candidate: bool(_POST_LINK.match(candidate)),
            find_targets=_posts_in,
            proxy=self._proxy,
            outside_message=f"{url} leads outside X",
        )


def _posts_in(text: str) -> list[str]:
    """Post links inside a t.co interstitial page."""
    return [match.group(0) for match in _POST_LINK.finditer(text)]
