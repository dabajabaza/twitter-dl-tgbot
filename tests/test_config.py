from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.helpers.factories import FAKE_BOT_TOKEN, GUEST_ID, OWNER_ID, build_settings
from twitter_dl.config import Settings


def test_owner_is_always_allowed_even_when_the_list_is_empty(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, allowed_ids_raw="")
    assert settings.allowed_ids == frozenset({OWNER_ID})


def test_guests_join_the_owner_in_the_whitelist(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, allowed_ids_raw=f" {GUEST_ID} , 7 ")
    assert settings.allowed_ids == frozenset({OWNER_ID, GUEST_ID, 7})


def test_a_typo_in_the_whitelist_kills_startup_naming_the_variable(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="ALLOWED_IDS"):
        build_settings(tmp_path, allowed_ids_raw="1,not-an-id")


def test_x_traffic_follows_the_telegram_proxy_unless_told_otherwise(tmp_path: Path) -> None:
    shared = build_settings(tmp_path, telegram_proxy="http://127.0.0.1:1080")
    assert shared.ytdlp_proxy == "http://127.0.0.1:1080"

    split = build_settings(
        tmp_path, telegram_proxy="http://127.0.0.1:1080", ytdlp_proxy_raw="http://127.0.0.1:1081"
    )
    assert split.ytdlp_proxy == "http://127.0.0.1:1081"


def test_no_proxy_configured_means_no_proxy_for_either_hop(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    assert settings.telegram_proxy is None
    assert settings.ytdlp_proxy is None


def test_the_chat_overflow_threshold_is_reported_in_bytes(tmp_path: Path) -> None:
    assert build_settings(tmp_path, max_tg_video_mb=50).max_tg_video_bytes == 50 * 1024 * 1024


def test_each_overflow_adapter_env_line_maps_an_id_to_a_full_factory_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "OVERFLOW_ADAPTERS__YANDEX_DISK",
        "twitter_dl.adapters.yandex_disk:create",
    )

    settings = Settings(
        bot_token=FAKE_BOT_TOKEN,
        owner_id=OWNER_ID,
        download_dir=tmp_path,
        _env_file=None,
    )

    assert settings.overflow_adapters == {"yandex_disk": "twitter_dl.adapters.yandex_disk:create"}


def test_a_malformed_optional_overflow_mapping_does_not_kill_core_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OVERFLOW_ADAPTERS", "not-a-mapping")

    settings = Settings(
        bot_token=FAKE_BOT_TOKEN,
        owner_id=OWNER_ID,
        download_dir=tmp_path,
        _env_file=None,
    )

    assert settings.overflow_adapters == {"configuration": "not-a-mapping"}


def test_adapter_owned_env_settings_do_not_look_unknown_to_core_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"TELEGRAM_BOT_TOKEN={FAKE_BOT_TOKEN}",
                f"OWNER_ID={OWNER_ID}",
                "OVERFLOW_ADAPTERS__YANDEX_DISK=twitter_dl.adapters.yandex_disk:create",
                "YANDEX_DISK_RCLONE_REMOTE=yandex:twitter-dl",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.overflow_adapters["yandex_disk"] == "twitter_dl.adapters.yandex_disk:create"


@pytest.mark.parametrize(
    ("field", "value"),
    [("queue_limit", 0), ("download_timeout_s", 0), ("max_tg_video_mb", 0)],
)
def test_nonsensical_limits_are_refused_at_startup(tmp_path: Path, field: str, value: int) -> None:
    with pytest.raises(ValidationError, match=field.upper()):
        build_settings(tmp_path, **{field: value})
