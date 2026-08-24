"""Bluesky, the first platform this bot learned after X.

One link shape, no wrapper: ``bsky.app/profile/<handle>/post/<rkey>``, where the
handle is either a domain-ish name (``alice.bsky.social``) or a raw
``did:plc:…``. Public posts need no session, so this Provider reads no settings
at all — which is the point of it going first.

Two spellings yt-dlp accepts are deliberately not accepted here: ``at://`` URIs
are not links a person shares in a chat, and ``main.bsky.dev`` is the staging
deployment. Neither is worth widening the surface for.
"""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.downloader import EngineProfile, YtDlpDownloader
from clipivore.services.providers import Provider, ProviderContext

# The rkey is the part the post is keyed on. The handle is as loose as Bluesky
# allows — a DID carries colons — while the tail matches X's: it stops at
# whitespace and at a comma, because that is how two links arrive glued together
# in forwarded text.
_POST_LINK = re.compile(
    r"https?://(?:www\.)?bsky\.app/profile/[\w.:%-]+/post/(?P<id>\w+)[^\s<>\"',]*",
    re.IGNORECASE,
)


# What the shared engine needs to know about Bluesky. A description of the
# platform, not of this installation — built once here, and reading it costs no
# configuration (see providers/x.py for the same shape).
PROFILE = EngineProfile(
    # Only Bluesky's own extractor. Its `_extract_videos` hands an external
    # embed straight to `url_result(external_uri)`, so without the lock a post
    # linking to a video elsewhere would come back as that stranger's video,
    # captioned with this post.
    allowed_extractors=("bluesky",),
    # Bluesky deletes rather than tombstones, and a blocked or deactivated
    # account reads the same way.
    account_state_markers=(
        "not found",
        "could not be found",
        "deleted",
        "deactivated",
        "suspended",
        "blocked",
    ),
    # No auth markers on purpose: this Provider holds no Cookie session, so it
    # can never be the cause of an Auth expiry alert. A sentence that looked
    # like one would wake the Owner over something they cannot fix.
    no_video_markers=("no video could be found",),
    unavailable_markers=("unavailable", "private"),
    profile_url=lambda handle: f"https://bsky.app/profile/{handle}",
)


class BlueskyProvider(Provider):
    name: ClassVar[str] = "Bluesky"
    hint: ClassVar[str] = "bsky.app/profile/<handle>/post/<id>"
    post_link: ClassVar[re.Pattern[str]] = _POST_LINK

    def __init__(self, context: ProviderContext) -> None:
        self._downloader = YtDlpDownloader(PROFILE, proxy=context.proxy)

    @property
    def downloader(self) -> Downloader:
        return self._downloader
