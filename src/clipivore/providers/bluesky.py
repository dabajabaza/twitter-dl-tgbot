"""Bluesky, the first platform this bot learned after Twitter.

One link shape, no wrapper: ``bsky.app/profile/<handle>/post/<rkey>``, where the
handle is either a domain-ish name (``alice.bsky.social``) or a raw
``did:plc:…``. Public posts need no session, so this Provider reads no settings
at all — which is the point of it going first.

Two spellings yt-dlp accepts are deliberately not accepted here: ``at://`` URIs
are not links a person shares in a chat, and ``main.bsky.dev`` is the staging
deployment. Neither is worth widening the surface for.

Two things Bluesky cannot currently answer well, both recorded in
ARCHITECTURE.md D19: a deleted or blocked post arrives as a plain download
failure rather than as "that post is gone", and a quote post that pairs an
external link card with the quoted post's own video yields "no video in this
post" — the card is refused by the extractor lock (D15) before the video is
reached. Both are the platform's shape meeting yt-dlp's, not a decision.
"""

import re
from typing import ClassVar

from clipivore.domain import Downloader
from clipivore.services.downloader import EngineProfile, YtDlpDownloader
from clipivore.services.providers import Provider, ProviderContext

# The rkey is the part the post is keyed on. The handle is as loose as Bluesky
# allows — a DID carries colons — while the tail matches Twitter's: it stops at
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
    # Empty, and not for lack of states worth naming. A deleted, blocked or
    # deactivated post is unreachable through yt-dlp's error text: the XRPC API
    # returns those as HTTP 400 with the reason only in the JSON body, which
    # yt-dlp drops, and a thread whose `$type` says notFoundPost raises a bare
    # KeyError that arrives as "an extractor error has occurred … please report
    # this issue". So such a post ends as a plain download failure rather than
    # "that post may be deleted", and markers here would only look like they
    # were doing something. Fixing it properly means reading the response body
    # in the engine, or an upstream `expected=True` — see ARCHITECTURE.md D19.
    account_state_markers=(),
    # No auth markers on purpose: this Provider holds no Cookie session, so it
    # can never be the cause of an Auth expiry alert. A sentence that looked
    # like one would wake the Owner over something they cannot fix.
    no_video_markers=("no video could be found",),
    # No `clean_description` either, and that is deliberate: `record.text` is
    # the author's text as typed, with links kept in facets alongside rather
    # than appended to it. There is no furniture to strip.
    #
    # A quote post's metadata describes the post being quoted — including its
    # id — so the id for external names is read from the link instead (D7).
    post_id_from_url=lambda url: BlueskyProvider.post_id(url),
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
