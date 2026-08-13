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
`TELEGRAM_PROXY=http://127.0.0.1:1080`, `COOKIES_FILE`, `RCLONE_CONFIG`.

### The rclone config: a copy of its own, not a shared one

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

## rc.d

```sh
install -m 755 /home/twitterdl/app/deploy/rc.d/twitter_dl /usr/local/etc/rc.d/twitter_dl
sysrc twitter_dl_enable=YES
```

The script is versioned in this repository (`deploy/rc.d/twitter_dl`, see
ARCHITECTURE.md D13); the copy on the server is updated by hand whenever the
original changes.

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

- **Public** (see above — otherwise the deploy never happens at all).
- `.github/workflows/ci.yml` with a job named exactly `ci`.
- Ruleset `protect-main`: restrict deletions and force pushes, require a pull
  request (0 approvals), linear history, require the `ci` status check, empty
  bypass list.
- Release immutability, so a tag cannot be moved between the CI check and the
  clone.
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
