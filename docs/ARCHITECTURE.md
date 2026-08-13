# Architectural decisions

Format: **Context → Decision → Consequences → Revisit when**. The numbers are
stable and are never renumbered — code comments refer to them
(`see ARCHITECTURE.md D7`).

One file instead of `docs/adr/NNNN-*.md`: for a one-person project
`git log -p docs/ARCHITECTURE.md` gives the history for free, and a single file
stays greppable.

The vocabulary (Owner, Guest, Clip, Share, Cookie session…) is in
[CONTEXT.md](../CONTEXT.md).

---

## D1. The guest list lives in an environment variable, not a database

**Context.** The neighbouring bots (`chain-health`, `lesson-tracker`) keep users
in SQLite, with sign-up, invites and roles. Here there are two or three users,
and all of them are people the owner knows personally.

**Decision.** `OWNER_ID` plus `ALLOWED_IDS` (a comma-separated list). The owner
is added to the list automatically and cannot accidentally lock themselves out.
There is no sign-up flow at all: to add someone, the owner edits the env file
and restarts the bot.

**Consequences.** No database, no migrations, no invite codes and no expiry for
them. Changing the roster requires access to the server — which, for "a personal
bot on personal credentials", is a feature rather than a shortcoming: every
extra user spends the owner's X account.

**Revisit when** there are more than a dozen users, or self-service sign-up
starts to make sense.

---

## D2. yt-dlp is a project dependency, not a system package

**Context.** yt-dlp is the one dependency that ages in days: X breaks its
internal API and the fix reaches PyPI the same day. In the FreeBSD ports the lag
runs to months, and the quarterly branch trails further still.

**Decision.** `yt-dlp` in `pyproject.toml`, pinned in `uv.lock`, installed with
pip into the bot's venv along with everything else. Used as a **library**
(`import yt_dlp`), not as a subprocess.

**Consequences.** Updating is a deliberate act (`uv lock --upgrade-package
yt-dlp` plus a new tag) rather than a side effect of a `pkg upgrade` at some
unrelated moment. Rolling a release back rolls yt-dlp back with it, because the
venv lives inside `releases/<tag>/`. As a library it provides progress hooks and
typed exceptions instead of stdout to parse — which is what D8 (progress) and
the failure taxonomy (`errors.py`) are built on.

**Revisit when** there is a reason to keep one yt-dlp for several consumers in
the jail.

---

## D3. The owner's cookies.txt grants the rights; expiry is detected reactively

**Context.** Public tweets download without any authentication, but NSFW,
age-gated content and protected accounts do not. yt-dlp dropped password login
long ago (broken on X's side), and the official API costs money and does not
hand over video anyway.

**Decision.** A Netscape-format `cookies.txt` exported from a browser sits on
the server (`/usr/local/etc/twitter-dl-cookies.txt`, 0600) and is handed to
yt-dlp for every request. Expiry is detected from a download failure
(`AuthExpired` in the taxonomy) rather than checked ahead of time.

**Consequences.** No background traffic under the cookies for a check that would
be stale an hour later. The price is learning about expiry on the first private
tweet rather than in advance.

**The owner's export is read-only input.** yt-dlp rewrites the cookie file it is
handed after *every* run (`YoutubeDL.__exit__` → `save_cookies()`), successful or
not, and does so in place — open plus truncate, no temp-and-rename. So the
original is never touched, and every request gets **its own** throwaway copy
inside its own scratch directory (`services/cookies.py`). Per request rather
than one shared file, because a download abandoned on timeout lives on for a
moment and rewrites its cookie file on the way out — with a shared file that
write would land exactly while the next request is reading it, and that request
would look unauthenticated (a false alert to the owner, and one that muffles the
next real one).

Deduplication of the alert rests on the identity of the original (`OwnerAlerts`
— one signal per export, replacing the file re-arms it). Keying on a file the
bot itself rewrites would send the owner an alert per private tweet. Counting
"successful downloads" is no good either: public tweets keep downloading with a
dead session, which is exactly why the breakage is easy to miss. An unreadable
original (the owner is replacing it right now) counts as "the same session"
rather than a new one — otherwise a duplicate would arrive at the very moment of
the swap.

The "already notified" flag is set **after** a successful send. Otherwise one
flap of the proxy at the wrong moment would permanently swallow the single
signal the whole arrangement exists for.

**A residual risk, accepted deliberately.** A protected account the owner
follows answers, with dead cookies, in exactly the same words as a stranger's
protected account ("not authorized to view this protected tweet") — the two
cannot be told apart from the message. Such a case lands in `TweetUnavailable`
and the owner is not woken. The choice favours a rare miss over a frequent false
alarm: an alert is worth exactly as much as it is believed. If protected
accounts turn out to be the main use case, a heuristic will be needed (waking
the owner after N consecutive private tweets refused while the cookies are
non-empty, say).

**Revisit when** cookies start expiring more often than once a month — then a
proactive check or automatic session refresh earns its keep.

---

## D4. Strangers get silence, not a refusal

**Context.** Bot usernames get enumerated by crawlers. Any answer — including a
polite "you may not" — confirms that the bot exists and is alive.

**Decision.** `AuthMiddleware` returns `None` without a single Bot API call. The
refusal goes to the log: WARNING the first time for each id, DEBUG for repeats,
with at most 256 ids tracked.

**Consequences.** The owner can see in the log that the bot was found, while the
crawler sees nothing. The gate is an outer middleware on `update`, ahead of the
filters, so a stranger's message never even reaches link parsing.

**Revisit when** the bot ever becomes public (at which point this whole decision
is void).

---

## D5. The queue is capped at five requests, counting the one in flight

**Context.** Forwarding a channel post with a dozen links takes a second; the
server will spend half an hour downloading them.

**Decision.** `RequestQueue` with a limit of 5. The limit counts the request
already downloading: "five" means five in the system, not five on top of the one
being worked on. The sixth link is refused with a message rather than dropped
silently. The remaining links in that message are not processed after a refusal
— repeating the refusal per link is just noise.

**Consequences.** The queue lives in memory and is lost on restart (see D9). The
position in line is shown only when it is greater than one; otherwise it would
be an extra Bot API call on every request.

---

## D6. All outbound traffic goes through the host's sing-box

**Context.** Neither Telegram nor X is reachable directly. The server's host
already runs sing-box (SOCKS/HTTP on `127.0.0.1:1080`, VLESS outbound), the
`bots` jail inherits the host's network stack (`ip4 = inherit`), and the
neighbouring bots take exactly the same route.

**Decision.** `TELEGRAM_PROXY=http://127.0.0.1:1080` for aiogram, and the same
address for yt-dlp (`YTDLP_PROXY` defaults to `TELEGRAM_PROXY` — it is the same
hop).

**Consequences.** The exit IP matches the one the owner browses X from, so from
X's point of view the cookies and the address are consistent. No second proxy
inside the jail is needed. If sing-box goes down, the bot answers "Can't reach X
right now" instead of hanging: proxy errors land in the `NetworkUnavailable`
class.

---

## D7. Size decides where a clip goes

**Context.** The Bot API will not let a bot upload a file larger than 50 MB.
Tweets from Premium accounts can run for an hour. Standing up a local Bot API
server (a 2 GB limit) means another daemon on an old laptop for a rare case.

**Decision.** Always download the best quality. A clip under the ceiling goes to
the chat, captioned with the tweet's link. A clip over it is copied by `rclone`
to the router's SMB share, and the chat gets a human-readable path. The answer
is the same for everyone: telling "on the LAN" from "a guest elsewhere" is not
worth the code — the case is rare, and an honest answer beats silently degrading
quality.

**Consequences.** The file name on the share (`<date>-<author>-<id>.mp4`) is its
only index: there is no retention and no listing. Hence sortable by date and
greppable by author. The metadata comes from X, so the name is sanitised hard:
everything but letters, digits, `_` and `-` is collapsed — dots included, so
that a `../..` in an account name means nothing once pasted into a path.

**Revisit when** large videos become a common case — then, a local Bot API
server.

---

## D8. One worker, sequential, with a thirty-minute ceiling

**Context.** There is one uplink (VLESS) and an old laptop CPU. Parallel
downloads would not finish sooner: they would share the same pipe.

**Decision.** A single queue consumer. Per request, `asyncio.timeout(1800)`
covering both the download and the delivery. Progress appears in one editable
message, edited no more than once every five seconds.

**Consequences.** Waiting time is predictable and can be stated ("2nd in line").
Throttling is mandatory: yt-dlp calls the progress hook dozens of times a
second, and Telegram starts rejecting edits long before that. The hook arrives
on a worker thread (`asyncio.to_thread`), so the downloader marshals it back
onto the loop with `call_soon_threadsafe` — callers never think about threads.

**A thread abandoned on timeout must also be stopped.** `asyncio.to_thread`
cannot be cancelled: the future is already RUNNING, `cancel()` returns False,
and yt-dlp keeps downloading in the background — taking the pipe from the next
request and blocking process shutdown on the executor join (up to 300 s, i.e.
`service twitter_dl restart` during a deploy). The one way in is the progress
hook: on cancellation the downloader sets a flag, and the very next hook call
raises, unwinding the thread.

**A verdict is final.** After `finish()`/`close()` the `ProgressReporter` enters
a terminal state: `set`/`offer` become no-ops. Without it the hook of a still
living zombie thread would, five seconds later, overwrite "Gave up after 30
minutes" with "Downloading… 63%", and the status would lie for the rest of that
thread's life.

**The deadline bounds the work, not the telling of it.** The final status update
(`_announce`) was moved outside `asyncio.timeout`: by then the clips are
delivered, the work is done, and there is nothing left to cancel. While it was
inside, a deadline expiring on that very last edit cancelled the edit itself —
the message froze on "Uploading…" forever, and on the share route it took the
path to the file with it, i.e. the share's only index (D7). The invariant "the
status always reaches a final state" is covered by a test that fails if the
verdict is put back under the deadline.

---

## D9. There is no state at all — no database, no file

**Context.** The neighbours have SQLite, alembic and a two-layer backup. Looking
at what of that is needed here: the user list is static (D1), the queue is
ephemeral, and nobody needs a history of requests.

**Decision.** No database. The process's state is the in-memory queue and the
"the owner has already been told about the cookies" flag. aiogram's FSM storage
is a null object too (`bot/storage.py`): there are no dialogs, and the default
`MemoryStorage` would create a record for everyone who writes in — strangers
included, because aiogram resolves the FSM context in a middleware registered
inside the `Dispatcher` constructor, i.e. **before** any gate of ours. That was
unbounded memory growth driven by the very people the bot refuses to answer.

**Consequences.** A restart is always clean, there is nothing to migrate, and
nothing to add to the backup role (`backup_dumps` is left alone; sanoid
snapshots the jail's dataset anyway). The price is that a restart loses the
queue; accepted deliberately, since "send the link again" is cheaper than a
persistent queue with recovery.

**Revisit when** statistics, or "I have already downloaded this tweet"
deduplication, become necessary.

---

## D10. Replies are English and plain text

**Context.** Replies quote things that do not belong to the bot: account
handles, yt-dlp messages, Windows paths full of backslashes.

**Decision.** No HTML or Markdown parse mode. Every string lives in
`bot/texts.py`, in English.

**Consequences.** There is nothing to escape and no markup to break. There is no
bold text, and the UX does not suffer for it. The rule is enforced by a test
(`test_texts.py`): Cyrillic in a string fails the build.

---

## D11. There is no DI container, unlike in the neighbouring bots

**Context.** `chain-health` uses dishka, and non-trivial machinery rests on it:
a database session per request, a unit of work, scope collapsing
(`_collapse_dishka_scopes`).

**Decision.** Here there is not a single request-scoped dependency, because
there is no database (D9). The singletons (`Settings`, `Bot`, `RequestQueue`,
the downloader, the delivery route) are assembled by hand in
`__main__._run_bot`, and handlers receive what they need through aiogram's
workflow data (`dp["queue"]`, `dp["settings"]`).

**Consequences.** One dependency fewer, and a whole class of scope traps gone.
The seams tests need are provided by the `Downloader` and `Delivery` protocols,
declared in `runtime/worker.py` — where they are consumed.

**Revisit when** per-request state appears (that is, if D9 is ever reversed).

---

## D12. Exactly one file knows about yt-dlp

**Context.** The first version kept `Clip` in `services/downloader.py`, so
importing the type dragged yt-dlp along with it — including into the worker,
which has no business knowing about the engine at all.

**Decision.** The domain types (`Clip`, `ProgressCallback`) live in `domain.py`,
free of both aiogram and yt-dlp. Only `services/downloader.py` may import
`yt_dlp`.

**Consequences.** The rule is checkable, and it is checked
(`test_architecture.py`). Replacing the engine is work in one file, and the
worker is tested with doubles without yt-dlp installed at all.

---

## D13. The rc.d script lives in the repository

**Context.** For both neighbours the rc.d script exists only on the server. In
`lesson-tracker` it is written down plainly: a drift between the two shows up in
no test — only in the server's log.

**Decision.** `deploy/rc.d/twitter_dl` is versioned here, next to the code it
launches. Installation is still manual (the `bot_deploy` ansible role renders no
templates), but there is a single source of truth, and a change to the
supervisor's timeout appears in the history alongside the code change.

**Consequences.** The copy on the server can still drift from the repository —
that is caught only by eye during a deploy; but now there is something to
compare against. The installation order is in [DEPLOY.md](DEPLOY.md).

---

## D14. The token lives in `TELEGRAM_BOT_TOKEN`, not `BOT_TOKEN`

**Context.** gitleaks' stock `telegram-bot-api-token` rule is gated on the word
"telegram" next to the secret: `TELEGRAM_BOT_TOKEN = "..."` is caught,
`BOT_TOKEN = "..."` is not. The neighbours use `BOT_TOKEN` and have to
compensate with a custom rule.

**Decision.** The variable is called `TELEGRAM_BOT_TOKEN`, and a custom rule
matching the token's shape (8-10 digits, a colon, 35 base64url characters) is
added anyway — together with a rule for X cookies.

**Consequences.** The secret is caught by both the stock rule and ours. The
variable name differs from the neighbours' — a deliberate divergence, not an
oversight.

---

## D15. yt-dlp is allowed to visit X and nowhere else

**Context.** A tweet with no media but an outbound link is handled by the
twitter extractor as `url_result(expanded_url)` — that is, it hands control to
the extractor for whatever site the link points to. Out of the box yt-dlp knows
1744 sites.

**Decision.** `allowed_extractors: ["twitter.*"]` — exactly six X extractors and
nothing else. An external URL produces "No suitable extractor found", which the
taxonomy files under `NoVideoInTweet`.

**Consequences.** A tweet linking to a video on someone else's site honestly
answers "that tweet has no video in it", instead of delivering that stranger's
video captioned with the tweet and filing it on the share under their metadata.
It also closes the path out of X through the owner's proxy: previously the
author of a tweet — a stranger — effectively chose where the bot would go. The
restriction is covered by a test.

**Revisit when** downloading from links inside tweets becomes desirable — but
that is a different product.

---

## D16. The worker's death must kill the process

**Context.** The worker is the only queue consumer. If it stops, the bot goes on
accepting links and answering "Queued…" while downloading nothing; the watchdog
meanwhile reports perfect health, because Telegram is reachable. From the
outside: a live bot that does nothing.

**Decision.** Two layers. First, the worker must not be able to die — `_say` and
`ProgressReporter` catch **any** exception, not just `TelegramAPIError` (aiogram
raises `ClientDecodeError`, a descendant of `AiogramError` but not of
`TelegramAPIError`, for instance when Telegram's front end serves an HTML 502
page instead of JSON). Second, an `add_done_callback` that, on any outcome other
than cancellation, sends the process SIGTERM — let the supervisor restart it.

**Consequences.** A transient 502 can no longer quietly behead the bot; and if
the worker does end for some unforeseen reason, it shows up as a restart rather
than as silence. Both layers are covered by tests.
