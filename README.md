# twitter-dl-tgbot

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
  the tweet's link. Anything larger is copied by `rclone` to the home SMB share,
  and the chat gets the path to it.
- The queue is strictly sequential: there is one uplink, and parallelism would
  only make progress reporting lie.
- Strangers get silence: the bot does not even confirm that it exists.

## Stack

Python 3.12, aiogram 3 (long polling), yt-dlp used as a library,
pydantic-settings, uv. No database whatsoever: the state is the environment,
the cookies and the log.

## Development

```sh
uv sync
cp .env.example .env      # fill in a test bot's token and OWNER_ID
uv run pre-commit install
uv run python -m twitter_dl
```

The same checks CI runs:

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src tests
```

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

1. An ordinary tweet with a video → the clip arrives, captioned with the link.
2. A tweet with several clips → all of them arrive, and the status message
   disappears after the last one.
3. A tweet with no video → "That tweet has no video in it".
4. Text with no links at all → "No tweet link found".
5. Six links at once → the sixth is refused with "Queue is full".
6. `MAX_TG_VIDEO_MB=1` → the share route: the file lands there and the chat gets
   the path.
7. A broken `COOKIES_FILE` plus an NSFW tweet → one alert to the owner, a polite
   refusal to whoever asked.
8. Proxy switched off for a minute → "Can't reach X right now", and the bot
   neither hangs nor dies.

## Operations

The bot lives as the third tenant of the `bots` jail on the home FreeBSD server
(user `twitterdl`, rc.d script `twitter_dl`, env file
`/usr/local/etc/twitter-dl.env`). Deployment is `ansible-pull` on a `vX.Y.Z`
tag with a green `ci` check; the details and the one-time bootstrap are in
[docs/DEPLOY.md](docs/DEPLOY.md).

Cookies expire — that is the normal course of things. The bot notices by itself
and tells the owner once per exported session: re-export `cookies.txt` from the
browser and replace the file on the server.

The decisions and the reasoning behind them are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); the domain vocabulary is in
[CONTEXT.md](CONTEXT.md).
