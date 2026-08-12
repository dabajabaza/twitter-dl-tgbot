"""Application configuration, loaded from the environment / ``.env`` file."""

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the bot needs to know, and nothing it can discover itself."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", populate_by_name=True
    )

    bot_token: str = Field(
        alias="TELEGRAM_BOT_TOKEN",
        description=(
            "Telegram Bot API token issued by @BotFather. Named TELEGRAM_BOT_TOKEN rather "
            "than BOT_TOKEN so gitleaks' keyword-gated stock rule catches a leak"
        ),
    )
    owner_id: int = Field(
        alias="OWNER_ID",
        description=(
            "Telegram user id of the owner: always allowed, and the only recipient of "
            "operational alerts such as expired X cookies"
        ),
    )
    # Kept as a raw string and parsed via the property below: pydantic-settings tries
    # to JSON-decode env values for complex field types (set/frozenset), which breaks
    # on a plain value like "1" or "1,2".
    allowed_ids_raw: str = Field(
        default="",
        alias="ALLOWED_IDS",
        description=(
            "Comma-separated Telegram user ids allowed to use the bot besides the owner; "
            "parsed by `allowed_ids`. Everyone else gets silence"
        ),
    )
    telegram_proxy: str | None = Field(
        default=None,
        alias="TELEGRAM_PROXY",
        description=(
            "Proxy URL for reaching api.telegram.org, e.g. http://127.0.0.1:1080. "
            "Required where the ISP blocks Telegram; leave unset for a direct connection"
        ),
    )
    ytdlp_proxy_raw: str | None = Field(
        default=None,
        alias="YTDLP_PROXY",
        description="Proxy URL for reaching X; falls back to TELEGRAM_PROXY when unset",
    )
    cookies_file: Path | None = Field(
        default=None,
        alias="COOKIES_FILE",
        description=(
            "Netscape-format cookies.txt of the owner's X session. Without it only public "
            "tweets download: no NSFW, no age-gated, no protected accounts"
        ),
    )
    download_dir: Path = Field(
        default=Path("/var/tmp/twitter-dl"),
        alias="DOWNLOAD_DIR",
        description="Scratch space; one subdirectory per request, removed when it finishes",
    )
    rclone_binary: str = Field(
        default="rclone",
        alias="RCLONE_BINARY",
        description="rclone executable used to copy oversized clips to the share",
    )
    rclone_config: Path | None = Field(
        default=None,
        alias="RCLONE_CONFIG",
        description=(
            "rclone config file holding the share's remote; unset uses rclone's own default"
        ),
    )
    rclone_remote: str = Field(
        default="keenetic:KeeneticShared/twitter-dl",
        alias="RCLONE_REMOTE",
        description="rclone destination (remote plus directory) for oversized clips",
    )
    share_path_prefix: str = Field(
        default=r"\\192.168.1.1\KeeneticShared\twitter-dl",
        alias="SHARE_PATH_PREFIX",
        description="How the share is named to a human; prepended to the file name in replies",
    )
    queue_limit: int = Field(
        default=5,
        alias="QUEUE_LIMIT",
        description="Requests in the system at once (waiting plus the one downloading)",
    )
    download_timeout_s: int = Field(
        default=1800,
        alias="DOWNLOAD_TIMEOUT_S",
        description="Seconds a single request may take end to end before it is abandoned",
    )
    max_tg_video_mb: int = Field(
        default=50,
        alias="MAX_TG_VIDEO_MB",
        description=(
            "Upload ceiling that splits chat delivery from share delivery. 50 is the Bot API "
            "limit; raising it only makes sense behind a local Bot API server"
        ),
    )

    @field_validator("cookies_file", "rclone_config", mode="before")
    @classmethod
    def _empty_path_means_unset(cls, value: object) -> object:
        """An empty value is "not configured", not the current directory.

        `.env.example` invites `NAME=` for optional settings, and pydantic turns
        an empty string into `Path('.')` for a `Path | None` field. That start
        succeeds and then fails on every single download, with yt-dlp trying to
        read cookies out of a directory.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def allowed_ids(self) -> frozenset[int]:
        """The whitelist: ALLOWED_IDS plus the owner, who can never lock themselves out."""
        try:
            guests = {int(chunk) for chunk in self.allowed_ids_raw.split(",") if chunk.strip()}
        except ValueError as exc:
            raise ValueError(
                f"ALLOWED_IDS must be comma-separated ints, got {self.allowed_ids_raw!r}"
            ) from exc
        return frozenset(guests | {self.owner_id})

    @property
    def ytdlp_proxy(self) -> str | None:
        """Proxy for X, defaulting to the Telegram one — they are the same hop here."""
        return self.ytdlp_proxy_raw or self.telegram_proxy

    @property
    def max_tg_video_bytes(self) -> int:
        """The chat/share threshold in bytes."""
        return self.max_tg_video_mb * 1024 * 1024

    @model_validator(mode="after")
    def _fail_fast_on_derived_values(self) -> "Settings":
        """Parse and range-check everything the properties above parse lazily, so a
        typo in the env file kills startup instead of surfacing on someone's first
        link — or, worse, on the first oversized clip hours later.
        """
        _ = self.allowed_ids
        if self.queue_limit < 1:
            raise ValueError(f"QUEUE_LIMIT must be >= 1, got {self.queue_limit}")
        if self.download_timeout_s < 1:
            raise ValueError(f"DOWNLOAD_TIMEOUT_S must be >= 1, got {self.download_timeout_s}")
        if self.cookies_file is not None and self.cookies_file.is_dir():
            raise ValueError(f"COOKIES_FILE must be a file, got directory {self.cookies_file}")
        if self.max_tg_video_mb < 1:
            raise ValueError(f"MAX_TG_VIDEO_MB must be >= 1, got {self.max_tg_video_mb}")
        return self
