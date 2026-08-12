# Выкатка

Бот живёт третьим в джейле `bots` на `acer-freebsd-srv` (192.168.1.118), рядом с `lesson-tracker`
и `chain-health`. Автоматика — роль `bot_deploy` из `automation/freebsd-server`, которую
`ansible-pull` применяет по крону раз в 2 минуты.

## Как работает автоматика

1. Раз в 2 минуты сервер тянет `automation` и применяет `freebsd-server/site.yml`.
2. Роль смотрит теги `vX.Y.Z` этого репозитория (анонимный HTTPS) и берёт самый свежий.
3. Тег деплоится, **только если** на его коммите есть зелёный check-run с именем ровно `ci`.
   Запрос к GitHub API анонимный, поэтому **репозиторий обязан быть публичным** — у приватного
   API отдаст 404, и это неотличимо от «CI не зелёный»: бот просто никогда не выкатится.
4. `releases/<tag>` клонируется, зависимости ставятся **pip'ом** из корневого `requirements.txt`
   (на сервере нет `uv`), `~/app` атомарно переключается на новый релиз, `service twitter_dl
   restart`, затем health-check с ретраями. Провал — откат симлинка и рестарт прежней версии.

Роль **не создаёт** ни пользователя, ни venv, ни rc.d-скрипт, ни env-файл. Это разовый ручной
бутстрап ниже.

## Разовый бутстрап сервера

Всё внутри джейла: `ssh root@192.168.1.118`, далее `jexec bots sh`.

```sh
# 1. Пользователь и его дом
pw useradd twitterdl -m -d /home/twitterdl -s /bin/sh

# 2. ffmpeg — без него yt-dlp не склеит раздельные видео- и аудиодорожки,
#    то есть тихо просядет качество (см. ARCHITECTURE.md D2)
pkg install -y ffmpeg

# 3. venv (роль его не создаёт, а путь до site-packages захардкожен под python3.12)
su -m twitterdl -c 'python3.12 -m venv /home/twitterdl/venv'

# 4. Ловушка первого запуска: без этого каталога роль пытается сделать
#    `mv ~/app ~/releases/pre-deploy`, падает и уходит в rescue
mkdir -p /home/twitterdl/releases/pre-deploy
chown -R twitterdl:twitterdl /home/twitterdl

# 5. Каталог для скачиваний
install -d -o twitterdl -g twitterdl -m 700 /var/tmp/twitter-dl
```

## Секреты (руками, копии — в KeePass)

```sh
# Токен от @BotFather, id владельца и гостей
install -m 600 -o twitterdl /dev/null /usr/local/etc/twitter-dl.env
# заполнить по образцу .env.example

# Куки X: выгрузить cookies.txt из браузера, скопировать на сервер
install -m 600 -o twitterdl cookies.txt /usr/local/etc/twitter-dl-cookies.txt
```

Обязательный минимум в env-файле: `TELEGRAM_BOT_TOKEN`, `OWNER_ID`,
`TELEGRAM_PROXY=http://127.0.0.1:1080`, `COOKIES_FILE`, `RCLONE_CONFIG`.

Проверить, что бот дотянется до шары под своим пользователем:

```sh
su -m twitterdl -c 'rclone --config /usr/local/etc/backup/rclone.conf lsd keenetic:'
su -m twitterdl -c 'rclone --config /usr/local/etc/backup/rclone.conf mkdir keenetic:KeeneticShared/twitter-dl'
```

## rc.d

```sh
install -m 755 /home/twitterdl/app/deploy/rc.d/twitter_dl /usr/local/etc/rc.d/twitter_dl
sysrc twitter_dl_enable=YES
```

Скрипт версионируется в этом репозитории (`deploy/rc.d/twitter_dl`, см. ARCHITECTURE.md D13);
копия на сервере обновляется вручную при изменении оригинала.

## Регистрация в деплое

В `automation/freebsd-server/site.yml`, список `deploy_bots`:

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

`backup_dumps` не трогаем: базы нет (ARCHITECTURE.md D9), а датасет джейла и так снапшотится
sanoid'ом.

## Требования к репозиторию на GitHub

- **Публичный** (см. выше, иначе деплой не состоится никогда).
- `.github/workflows/ci.yml` с job'ом, чьё имя ровно `ci`.
- Ruleset `protect-main`: запрет удаления и force-push, PR обязателен (0 апрувов), линейная
  история, обязательный статус-чек `ci`, пустой bypass-список.
- Release immutability — чтобы тег нельзя было передвинуть между проверкой CI и клоном.
- Теги строго `vX.Y.Z`. Откат — **новым тегом**, никогда не передвиганием старого.

## Релиз

```sh
uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt  # если менялись зависимости
git tag v0.1.0 && git push origin v0.1.0
```

Дальше сервер сам, в течение двух минут. Что произошло — видно так:

```sh
tail -f /var/log/ansible-pull.log          # на хосте
grep bot-deploy /var/log/messages          # вердикт роли
jexec bots service twitter_dl status
```

Красный CI → тег молча игнорируется, в `/var/log/messages` появляется запись
`... skipped, ci not green`.
