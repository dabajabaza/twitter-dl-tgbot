# clipivore-tgbot

A personal Telegram bot: send it a link to a post on X and get the video from
it, at the best quality available. Downloads run through `yt-dlp` under the
owner's own cookies, so NSFW, age-gated and protected accounts the owner
follows are all reachable.

Public downloader bots live off farms of throwaway accounts and bought
residential proxies, and pay for it with forced channel subscriptions, ads and
an HD paywall. A personal bot has none of that economy: two or three users, one
real account, one uplink.

## What it does

- A link in a message (`x.com`, `twitter.com`, `t.co`) becomes a video in the
  chat. There is no command to remember — the link is the command.
- Several links in one message, and several clips in one tweet, are all handled
  in turn.
- A clip up to 50 MB (the Bot API ceiling) arrives in the chat, captioned with
  the tweet's text and a footer — `@author · Open in X` — linking the author's
  profile and the tweet. Once everything a message asked for arrived, the
  message with the link is deleted; any failure leaves it in place. Delivery of
  larger clips is optional: the owner can switch between the configured
  Overflow Adapters from the bot's Menu.
- Built-in Overflow Adapters cover an SMB Share and Yandex Disk through
  `rclone`; every Adapter found in `src/clipivore/adapters/` appears in the
  Menu automatically, ready or not. A custom Adapter is one module in that
  package plus its environment configuration.
- The queue is strictly sequential: there is one uplink, and parallelism would
  only make progress reporting lie.
- The status message says what is moving and how much: `Downloading video… 47%
  of 82 MB`, then the audio stream, then `Uploading to Telegram… (82 MB)`. A
  size-limit refusal quotes the size it learned: how far an aborted download
  got, or the finished clip's actual size.
- Strangers get silence: the bot does not even confirm that it exists.

## Stack

Python 3.12, aiogram 3 (long polling), yt-dlp used as a library,
pydantic-settings, uv. No database: the only durable application value is the
Owner's selected Overflow Adapter in one small state file.

## Development

```sh
uv sync
cp .env.example .env      # fill in a test bot's token and OWNER_ID
uv run pre-commit install
uv run python -m clipivore
```

The same checks CI runs:

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src tests
```

### Adding an Overflow Adapter

Drop one module into `src/clipivore/adapters/` holding exactly one concrete
subclass of the fixed interface — the file name is the Adapter's stable id,
the `label` class attribute is its Menu name:

```python
from pathlib import Path

from clipivore.services.overflow import OverflowDestination


class MyDestination(OverflowDestination):
    label = "My storage"

    # Constructed with no arguments: read your own MY_STORAGE_* settings here.
    # Store source and return the non-empty locator shown in chat.
    async def store(self, source: Path, *, name: str) -> str: ...
```

The subclass owns and validates its prefixed environment settings, like the
built-ins next to it; modules whose names start with an underscore are shared
helpers and are not scanned. Menu discovers the Adapter at startup — there is
nothing to register.

A module that fails to import or construct marks only that Adapter
unavailable — a failed construction is reported under its own class label, a
failed import under a name derived from its file (the class never came to
exist). A subdirectory in the package is refused visibly as misconfigured.
None of it prevents the bot or Chat delivery from starting.

`requirements.txt` is generated from `uv.lock` and must agree with it (the
server has no `uv`; it installs with pip):

```sh
uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt
```

Updating yt-dlp — the one dependency that ages in days rather than months:

```sh
uv lock --upgrade-package yt-dlp && uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt
```

## Manual run-through before a release

The automated tests never touch the network, so the X → yt-dlp → Telegram chain
is only ever exercised by a person. With a test token:

1. An ordinary tweet with a video → the clip arrives, captioned with the
   tweet's text and a footer whose `@author` opens the profile and whose
   "Open in X" opens the tweet; the message with the link disappears.
2. A tweet with several clips → all of them arrive, and the status message
   disappears after the last one.
3. A tweet with no video → "That tweet has no video in it", and the message
   with the link stays.
4. Text with no links at all → "No tweet link found".
5. Six links at once → the sixth is refused with "Queue is full".
6. `MAX_TG_VIDEO_MB=1` with Overflow delivery off → an explicit size-limit
   verdict naming how far the download got, and no complete oversized download.
7. Enable the Share Adapter → the file lands there and the chat gets the path
   plus the tweet's text and footer; enable Yandex Disk → the chat gets a
   working public link.
8. One good link and one dead link in the same message → the good one arrives,
   and the message stays.
9. Remove or break the selected Adapter → small clips still arrive, and a large
   one names the missing or misconfigured Overflow destination.
10. A broken `COOKIES_FILE` plus an NSFW tweet → one alert to the owner, a
   polite refusal to whoever asked.
11. Proxy switched off for a minute → "Can't reach X right now", and the bot
   neither hangs nor dies.

## Operations

The bot lives as the third tenant of the `bots` jail on the home FreeBSD server
(user `twitterdl`, rc.d script `twitter_dl`, env file
`/usr/local/etc/twitter-dl.env`). Those names are older than the bot's own:
they were left behind by the rename to `clipivore` because they were created by
hand on a host reachable only through `ansible-pull`. Renaming them is a
sit-down at the server, written out in
[docs/SERVER-RENAME.md](docs/SERVER-RENAME.md). Deployment is `ansible-pull` on
a `vX.Y.Z` tag with a green `ci` check; the details and the one-time bootstrap
are in [docs/DEPLOY.md](docs/DEPLOY.md).

Cookies expire — that is the normal course of things. The bot notices by itself
and tells the owner once per exported session: re-export `cookies.txt` from the
browser and replace the file on the server.

The decisions and the reasoning behind them are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); the domain vocabulary is in
[CONTEXT.md](CONTEXT.md).
