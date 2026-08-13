# Domain glossary

The words the code, the logs and the bot's replies all use. No implementation
details here — those live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## People

**Owner** — the single person who owns the bot. Their X account is what gives it
any rights at all (see *Cookie session*), and they are the only recipient of
operational alerts. Always on the guest list, even if their id was left out of
`ALLOWED_IDS`.

**Guest** — an allowed user who is not the Owner. Same download rights, no
alerts. Every request of theirs runs under the Owner's account — which is why
there are only ever a handful of them.

**Stranger** — anybody else. Gets silence rather than a refusal: an answer of
any kind would confirm that the bot exists and is alive.

## The work

**Tweet link** — a URL naming a single post on X. It arrives either direct
(`x.com/<user>/status/<id>`, plus the historical `twitter.com` and `mobile.`
spellings) or wrapped (`t.co/<slug>`, which says nothing about its target until
it is followed).

**Request** — one link accepted from one user. It holds one queue slot and
lives until it has a verdict. The same link sent twice in one message produces
a single Request.

**Clip** — one video file extracted from a tweet. A tweet may yield several; an
X "GIF" is a Clip too (a looping, audio-less mp4), not a separate kind of thing.

**Verdict** — how a Request ended: the clips were delivered, or the reason was
named. Silence is never an outcome — the status message always reaches a final
state.

## Delivery

**Chat delivery** — the Clip went to the chat. Possible while it fits under the
Bot API ceiling (50 MB).

**Share delivery** — the Clip did not fit and went to the *Share* instead; the
chat gets its path.

**Share** — the SMB directory on the home router (`KeeneticShared/twitter-dl`)
where clips too large for Telegram are filed. There is no retention policy: the
file name is its only index.

## Access to X

**Cookie session** — the Owner's X session, exported from a browser
(`cookies.txt`). It is how the bot identifies itself to X; without it only
public tweets are reachable.

**Auth expiry** — the state in which the Cookie session no longer
authenticates. Treacherous because public tweets keep downloading: from the
outside everything still "works" while NSFW, age-gated and protected content
quietly stops. The only breakage worth waking the Owner for.
