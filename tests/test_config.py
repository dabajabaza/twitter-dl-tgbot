from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.helpers.factories import GUEST_ID, OWNER_ID, build_settings


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


def test_the_chat_share_threshold_is_reported_in_bytes(tmp_path: Path) -> None:
    assert build_settings(tmp_path, max_tg_video_mb=50).max_tg_video_bytes == 50 * 1024 * 1024


@pytest.mark.parametrize(
    ("field", "value"),
    [("queue_limit", 0), ("download_timeout_s", 0), ("max_tg_video_mb", 0)],
)
def test_nonsensical_limits_are_refused_at_startup(tmp_path: Path, field: str, value: int) -> None:
    with pytest.raises(ValidationError, match=field.upper()):
        build_settings(tmp_path, **{field: value})
