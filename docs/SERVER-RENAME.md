# Renaming the server side

The rename to `clipivore` stopped at the repository border. Inside the jail the
bot is still `twitterdl`: a unix user, an rc.d service, three hand-installed
files in `/usr/local/etc`, a scratch directory, a Kuma push key and two rclone
paths. None of them were created by ansible, so none of them can be renamed by
pushing a commit — hence this document rather than a role.

Nothing here is urgent. The names are invisible to everyone except ansible and
whoever reads `ps`, and the deploy works exactly as well with them. What they
cost is the small confusion of a `clipivore` release restarting a service called
`twitter_dl`. When that stops being funny, sit down and do this.

Everything is one sitting: the middle of the list has the bot down. Steps
marked **[auto]** are commits to the `automation` repository, pushed at that
point in the order — the cron pull applies them within two minutes.

## Before you start

- SSH to the host and to the jail: `ssh root@192.168.1.118`, then `jexec bots sh`.
- Have KeePass open: the env file and the rclone config are copied there, and
  both change.
- Pick a quiet moment. Between step 1 and step 10 the bot does not answer.

## 1. Freeze the deploy

On the **host** (not the jail), comment out the poller so no tick lands
mid-rename:

```sh
crontab -e     # comment: */2 * * * * /usr/local/sbin/ansible-pull-bots
```

Then in the jail:

```sh
service twitter_dl stop
sysrc twitter_dl_enable=NO
```

## 2. The user and its home

```sh
pw usermod twitterdl -l clipivore -d /home/clipivore
mv /home/twitterdl /home/clipivore
```

`releases/`, the `app` symlink, `notify.sock` and `.deps-sha256` all move with
the directory.

## 3. Recreate the venv

The venv stores absolute paths, so moving the home breaks it. Recreating it is
cheaper than repairing it, and it disposes of the stale `twitter-dl` editable
distribution left behind by the rename release for free:

```sh
su -m clipivore -c 'python3.12 -m venv /home/clipivore/venv'
rm /home/clipivore/.deps-sha256
```

Deleting `.deps-sha256` is what makes the next deploy tick run `pip install`
again instead of trusting the hash of a requirements file it already installed.

## 4. The hand-installed files

```sh
mv /usr/local/etc/twitter-dl.env         /usr/local/etc/clipivore.env
mv /usr/local/etc/twitter-dl-cookies.txt /usr/local/etc/clipivore-cookies.txt
mv /usr/local/etc/twitter-dl-rclone.conf /usr/local/etc/clipivore-rclone.conf
```

Then edit `/usr/local/etc/clipivore.env` — every path in it just moved, and the
two rclone remotes are renamed in step 6:

```sh
COOKIES_FILE=/usr/local/etc/clipivore-cookies.txt
DOWNLOAD_DIR=/var/tmp/clipivore
SHARE_RCLONE_CONFIG=/usr/local/etc/clipivore-rclone.conf
SHARE_RCLONE_REMOTE=keenetic:KeeneticShared/clipivore
SHARE_PATH_PREFIX='\\192.168.1.1\KeeneticShared\clipivore'
YANDEX_DISK_RCLONE_CONFIG=/usr/local/etc/clipivore-rclone.conf
YANDEX_DISK_RCLONE_REMOTE=yandex:clipivore
```

Update the KeePass copies of the env file and the rclone config to match.

## 5. Scratch space, and the one piece of state

`/var/tmp/twitter-dl` holds `.overflow-destination` — the Owner's selected
Overflow Adapter, and the only durable value the bot has (ARCHITECTURE.md D9).
Move it rather than letting the bot start with the selection reset:

```sh
install -d -o clipivore -g clipivore -m 700 /var/tmp/clipivore
mv /var/tmp/twitter-dl/.overflow-destination* /var/tmp/clipivore/ 2>/dev/null
rm -rf /var/tmp/twitter-dl
```

The glob also catches a `.recovery` or `.corrupt-*` sibling if an earlier write
was interrupted; those are part of the same protocol and belong together.

## 6. The share and the cloud

The Share Adapter writes to a folder on the router, which only the router's web
UI can rename: log in and rename `KeeneticShared/twitter-dl` to
`KeeneticShared/clipivore`. Then, in the jail:

```sh
su -m clipivore -c 'rclone --config /usr/local/etc/clipivore-rclone.conf lsd keenetic:KeeneticShared'
su -m clipivore -c 'rclone --config /usr/local/etc/clipivore-rclone.conf moveto yandex:twitter-dl yandex:clipivore'
```

Yandex keeps published links working across a move, but verify one of the links
the bot handed out rather than taking that on faith.

## 7. The heartbeat

The Kuma push key is derived from the rc.d `--name` argument
(`sdnotify-supervise`, `kuma_url()`), so it changes with the rename and the
monitor goes silent until both ends agree:

- In the Kuma UI, rename the push monitor `twitter-dl` to `clipivore`. The push
  token itself does not change.
- In `/usr/local/etc/kuma-push.conf`, rename `KUMA_PUSH_TWITTER_DL` to
  `KUMA_PUSH_CLIPIVORE`. That file is deliberately outside git.

## 8. [auto] The rc.d script and the deploy registration

In `automation`:

```sh
git mv freebsd-server/roles/bot_rc/files/twitter_dl \
       freebsd-server/roles/bot_rc/files/clipivore
```

Inside that file, rename everything that spells the bot: `PROVIDE`, `name`,
`rcvar`, `pidfile` (`/var/run/clipivore.pid`), the `twitter_dl_runas` / `_home`
/ `_envfile` defaults, `--name clipivore`, and `LOCK_FILE`
(`/home/clipivore/clipivore.lock`). In `freebsd-server/site.yml`, the same
entry's `name`, `service`, `home`, `owner` and `env_file`.

Push it. Do not unfreeze the cron yet — step 10 does that deliberately, after
the switch below.

## 9. Clean up what ansible will not

`bot_rc` copies its script in and never deletes the old one, so the previous
service would otherwise linger, enabled, pointing at a home that no longer
exists:

```sh
rm /usr/local/etc/rc.d/twitter_dl
sysrc -x twitter_dl_enable
rm -f /var/run/twitter-dl.pid /home/clipivore/twitter-dl.lock
```

## 10. Unfreeze

```sh
sysrc clipivore_enable=YES
```

Uncomment the cron line on the host, then run one pull by hand rather than
waiting for the tick, so a failure is in front of you:

```sh
/usr/local/sbin/ansible-pull-bots
tail -f /var/log/ansible-pull.log
grep bot-deploy /var/log/messages
jexec bots service clipivore status
```

Finally send the bot a link from Telegram, and check the Kuma monitor is green
again.

## 11. [repo] Catch the repository up

The defaults in this repository still describe the old server. Once the server
is renamed they are wrong, and a fresh checkout would point at directories that
no longer exist:

- `src/clipivore/config.py`: `download_dir` default → `/var/tmp/clipivore`.
- `.env.example`: the `COOKIES_FILE`, `DOWNLOAD_DIR` and rclone lines.
- `README.md`, `docs/DEPLOY.md`, `docs/ARCHITECTURE.md`: drop the "these names
  are older than the bot" notes, and this document with them.
