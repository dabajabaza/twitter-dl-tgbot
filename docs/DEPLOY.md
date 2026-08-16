# Deployment

The bot lives as the third tenant of the `bots` jail on `acer-freebsd-srv`
(192.168.1.118), next to `lesson-tracker` and `chain-health`. The automation is
the `bot_deploy` role from `automation/freebsd-server`, applied by
`ansible-pull` from cron every two minutes.

## How the automation works

1. Every two minutes the server pulls `automation` and applies
   `freebsd-server/site.yml`.
2. The role looks at this repository's `vX.Y.Z` tags (anonymous HTTPS) and takes
   the newest one.
3. A tag is deployed **only if** its commit carries a green check-run named
   exactly `ci`. That API call is anonymous, so the **repository must be
   public** — for a private one the API returns 404, which is indistinguishable
   from "CI is not green": the bot would simply never be rolled out.
4. `releases/<tag>` is cloned, dependencies are installed **with pip** from the
   root `requirements.txt` (the server has no `uv`), `~/app` is switched
   atomically to the new release, `service twitter_dl restart` runs, and then a
   health check with retries. On failure the symlink is rolled back and the
   previous version restarted.

The role creates **neither** the user, nor the venv, nor the rc.d script, nor
the env file. That is the one-time manual bootstrap below.

## One-time server bootstrap

All of it inside the jail: `ssh root@192.168.1.118`, then `jexec bots sh`.

```sh
# 1. The user and its home
pw useradd twitterdl -m -d /home/twitterdl -s /bin/sh

# 2. ffmpeg — without it yt-dlp cannot merge separate video and audio streams,
#    which silently caps quality (see ARCHITECTURE.md D2)
pkg install -y ffmpeg

# 3. The venv (the role does not create it, and the site-packages path is
#    hardcoded for python3.12)
su -m twitterdl -c 'python3.12 -m venv /home/twitterdl/venv'

# 4. The first-run trap: without this directory the role tries
#    `mv ~/app ~/releases/pre-deploy`, fails, and falls into its rescue path
mkdir -p /home/twitterdl/releases/pre-deploy
chown -R twitterdl:twitterdl /home/twitterdl

# 5. Scratch space for downloads
install -d -o twitterdl -g twitterdl -m 700 /var/tmp/twitter-dl
```

## Secrets (by hand; copies belong in KeePass)

```sh
# The @BotFather token, the owner's id and the guests'
install -m 600 -o twitterdl /dev/null /usr/local/etc/twitter-dl.env
# fill it in following .env.example

# X cookies: export cookies.txt from the browser, copy it to the server
install -m 600 -o twitterdl cookies.txt /usr/local/etc/twitter-dl-cookies.txt
```

The minimum the env file must carry: `TELEGRAM_BOT_TOKEN`, `OWNER_ID`,
`TELEGRAM_PROXY=http://127.0.0.1:1080`, and `COOKIES_FILE`. Overflow delivery
is optional; without any `OVERFLOW_ADAPTERS__*` entries the bot remains fully
functional for clips within Telegram's limit.

### Optional Overflow Adapters

The Owner selects the active Adapter with Menu → Overflow delivery. The choice
survives restarts in `/var/tmp/twitter-dl/.overflow-destination`; it does not
need database backup.

Each configured key maps a stable id to a full factory path:

```sh
OVERFLOW_DEFAULT=none
OVERFLOW_ADAPTERS__SHARE=twitter_dl.adapters.share:create
OVERFLOW_ADAPTERS__YANDEX_DISK=twitter_dl.adapters.yandex_disk:create
```

Missing factories and invalid Adapter settings do not prevent startup. They
appear as unavailable in Menu; Chat delivery continues to work.

#### The rclone config: a copy of its own, not a shared one

The `backup` role's config (`/usr/local/etc/backup/rclone.conf`) is out of the
bot's reach: the directory itself is root-only, and loosening the permissions is
pointless because the next ansible run puts them back. There are no secrets in
the file (share access is guest), so the bot gets its own copy:

```sh
install -m 600 -o twitterdl -g twitterdl \
    /usr/local/etc/backup/rclone.conf /usr/local/etc/twitter-dl-rclone.conf

# Check access and create the directory on the share
su -m twitterdl -c 'rclone --config /usr/local/etc/twitter-dl-rclone.conf lsd keenetic:'
su -m twitterdl -c 'rclone --config /usr/local/etc/twitter-dl-rclone.conf mkdir keenetic:KeeneticShared/twitter-dl'
```

If the share's host or name ever changes, this copy has to be updated by hand —
it deliberately lives outside ansible.

Enable the Share Adapter in `/usr/local/etc/twitter-dl.env`:

```sh
OVERFLOW_ADAPTERS__SHARE=twitter_dl.adapters.share:create
SHARE_RCLONE_CONFIG=/usr/local/etc/twitter-dl-rclone.conf
SHARE_RCLONE_REMOTE=keenetic:KeeneticShared/twitter-dl
SHARE_PATH_PREFIX='\\192.168.1.1\KeeneticShared\twitter-dl'
```

#### Yandex Disk

rclone already includes a Yandex Disk backend, so the bot needs no additional
Python dependency. Configure a remote in the bot-owned config; the interactive
wizard explains how to authorize from another machine when the jail has no
browser:

```sh
su -m twitterdl -c 'rclone --config /usr/local/etc/twitter-dl-rclone.conf config'
su -m twitterdl -c 'rclone --config /usr/local/etc/twitter-dl-rclone.conf mkdir yandex:twitter-dl'
```

Unlike the guest Share config, the Yandex remote contains an OAuth token: keep
the file owned by `twitterdl` with mode `600` and keep its recovery copy in
KeePass. Then enable the Adapter:

```sh
OVERFLOW_ADAPTERS__YANDEX_DISK=twitter_dl.adapters.yandex_disk:create
YANDEX_DISK_RCLONE_CONFIG=/usr/local/etc/twitter-dl-rclone.conf
YANDEX_DISK_RCLONE_REMOTE=yandex:twitter-dl
```

The Adapter runs `rclone copyto` followed by `rclone link`. The returned link is
public and has no automatic retention; removing or unpublishing it is an Owner
operation.

## rc.d

The rc.d script is not in this repository and needs no installing: it lives in
`automation/freebsd-server/roles/bot_rc/files/twitter_dl` and is deployed by
ansible, which also restarts the service when the script changes (see
ARCHITECTURE.md D13). What remains manual is the launch switch itself:

```sh
sysrc twitter_dl_enable=YES
```

Deliberately manual — the role never touches `rc.conf`, so a bot that is not
ready (no secrets, no first tag) cannot be started by a deploy tick.

## Registering with the deploy

In `automation/freebsd-server/site.yml`, the `deploy_bots` list:

```yaml
      - name: twitter-dl
        service: twitter_dl
        home: /home/twitterdl
        owner: twitterdl
        repo_owner: dabajabaza
        repo_name: twitter-dl-tgbot
        editable_pth: _editable_impl_twitter_dl.pth
        editable_target: /home/twitterdl/app/src
        alembic: false
        env_file: /usr/local/etc/twitter-dl.env
```

`backup_dumps` is left alone: there is no database (ARCHITECTURE.md D9), and the
jail's dataset is snapshotted by sanoid anyway.

## What the GitHub repository must look like

All of it is applied by `automation/scripts/new-bot-repo.sh`, which copies the
settings and rulesets from an already-configured bot repository rather than
having them clicked in again. Nothing below needs doing by hand.

- **Public** (see above — otherwise the deploy never happens at all).
- `.github/workflows/ci.yml` with a job named exactly `ci`.
- Ruleset `protect-main`: restrict deletions and force pushes, require a pull
  request (0 approvals), linear history, require the `ci` status check, empty
  bypass list.
- A tag ruleset (`immutable-version-tags`) forbidding `update`, `deletion` and
  force pushes on `refs/tags/v*`, so a tag cannot be moved between the CI check
  and the clone.

  Not the "Release immutability" checkbox in Settings: that one has no API at
  all, and it only binds tags carrying a *published Release* — while the deploy
  pushes bare tags, which it would never have covered.
- Tags strictly `vX.Y.Z`. Roll back with a **new tag**, never by moving an old
  one.

## Releasing

```sh
uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt  # if dependencies changed
git tag v0.1.0 && git push origin v0.1.0
```

The server takes it from there, within two minutes. What happened is visible
like this:

```sh
tail -f /var/log/ansible-pull.log          # on the host
grep bot-deploy /var/log/messages          # the role's verdict
jexec bots service twitter_dl status
```

A red CI means the tag is ignored silently, with a `... skipped, ci not green`
line appearing in `/var/log/messages`.
