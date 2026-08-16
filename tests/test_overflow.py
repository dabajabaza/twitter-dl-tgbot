"""Configured Adapter loading and the Owner's persisted choice."""

from pathlib import Path

import pytest

from twitter_dl.services import overflow as module
from twitter_dl.services.overflow import OverflowCatalog, OverflowState

GOOD = "tests.helpers.overflow_adapters:create"
BAD_CONFIG = "tests.helpers.overflow_adapters:misconfigured"


def catalog(
    tmp_path: Path, adapters: dict[str, object], *, default: str = "none"
) -> OverflowCatalog:
    return OverflowCatalog(adapters, default=default, state_file=tmp_path / "selection")


def test_a_full_factory_path_becomes_a_ready_menu_choice(tmp_path: Path) -> None:
    choices = catalog(tmp_path, {"test": GOOD}).choices

    assert len(choices) == 1
    assert choices[0].state is OverflowState.READY
    assert choices[0].label == "Test destination"


def test_a_missing_factory_is_a_visible_state_not_a_startup_failure(tmp_path: Path) -> None:
    choices = catalog(tmp_path, {"gone": "twitter_dl.adapters.gone:create"}).choices

    assert choices[0].state is OverflowState.MISSING


def test_invalid_adapter_settings_are_isolated_from_the_bot(tmp_path: Path) -> None:
    choices = catalog(tmp_path, {"broken": BAD_CONFIG}).choices

    assert choices[0].state is OverflowState.MISCONFIGURED


def test_a_malformed_factory_value_is_isolated_from_the_bot(tmp_path: Path) -> None:
    choices = catalog(tmp_path, {"broken": {"path": GOOD}}).choices

    assert choices[0].state is OverflowState.MISCONFIGURED


def test_a_factory_attribute_lookup_failure_is_isolated(tmp_path: Path) -> None:
    choices = catalog(
        tmp_path,
        {"broken": "tests.helpers.overflow_adapters:exploding_attribute"},
    ).choices

    assert choices[0].state is OverflowState.MISCONFIGURED


def test_system_exit_from_a_factory_is_isolated(tmp_path: Path) -> None:
    choices = catalog(tmp_path, {"broken": "sys:exit"}).choices

    assert choices[0].state is OverflowState.MISCONFIGURED


def test_system_exit_during_module_import_is_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exit_on_import(name: str) -> object:
        raise SystemExit("bad plugin")

    monkeypatch.setattr(module.importlib, "import_module", exit_on_import)

    choices = catalog(tmp_path, {"broken": "bad.plugin:create"}).choices

    assert choices[0].state is OverflowState.MISCONFIGURED


def test_the_validated_adapter_label_is_read_only_once(tmp_path: Path) -> None:
    choices = catalog(
        tmp_path,
        {"stateful": "tests.helpers.overflow_adapters:stateful_label"},
    ).choices

    assert choices[0].state is OverflowState.READY
    assert choices[0].label == "Read once"


@pytest.mark.parametrize(
    "factory",
    [
        "tests.helpers.overflow_adapters:duck",
        "tests.helpers.overflow_adapters:synchronous",
        "tests.helpers.overflow_adapters:wrong_signature",
    ],
)
def test_only_the_enforced_async_interface_becomes_ready(tmp_path: Path, factory: str) -> None:
    choices = catalog(tmp_path, {"broken": factory}).choices

    assert choices[0].state is OverflowState.MISCONFIGURED


def test_the_owner_selection_survives_a_restart(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    first = OverflowCatalog({"test": GOOD}, default="none", state_file=state_file)
    first.select("test")

    restarted = OverflowCatalog({"test": GOOD}, default="none", state_file=state_file)

    assert restarted.current.adapter_id == "test"
    assert restarted.current.ready


@pytest.mark.parametrize("broken_file", ["directory", "invalid-utf8", "empty"])
def test_an_unreadable_selection_disables_only_overflow_without_using_the_default(
    tmp_path: Path, broken_file: str
) -> None:
    state_file = tmp_path / "selection"
    if broken_file == "directory":
        state_file.mkdir()
    elif broken_file == "empty":
        state_file.write_text("  \n")
    else:
        state_file.write_bytes(b"\xff")

    restarted = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)

    assert restarted.current.adapter_id == module.SAVED_SELECTION_ID
    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_selecting_after_a_non_file_error_quarantines_it_and_recovers(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    restarted = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)

    restarted.select("none")

    assert state_file.is_file()
    assert state_file.read_text(encoding="utf-8").strip() == "none"
    assert (tmp_path / "selection.corrupt").is_dir()
    assert restarted.current.state is OverflowState.OFF


def test_a_state_symlink_is_misconfigured_and_quarantined_on_recovery(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("none\n")
    state_file = tmp_path / "selection"
    state_file.symlink_to(target)
    restarted = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)

    assert restarted.current.state is OverflowState.MISCONFIGURED

    restarted.select("none")

    assert state_file.is_file() and not state_file.is_symlink()
    assert target.read_text(encoding="utf-8") == "none\n"
    assert (tmp_path / "selection.corrupt").is_symlink()


def test_a_predictable_old_temp_symlink_is_never_followed(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    (tmp_path / ".selection.tmp").symlink_to(victim)
    choices = OverflowCatalog({}, default="none", state_file=state_file)

    choices.select("none")

    assert victim.read_text(encoding="utf-8") == "keep me"
    assert state_file.is_file() and not state_file.is_symlink()


def test_staging_failure_leaves_a_non_regular_state_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    choices = OverflowCatalog({}, default="none", state_file=state_file)

    def fail_staging(*args: object, **kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", fail_staging)

    with pytest.raises(OSError, match="disk full"):
        choices.select("none")
    assert state_file.is_dir()
    assert not (tmp_path / "selection.corrupt").exists()


def test_replace_failure_rolls_a_quarantined_state_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    choices = OverflowCatalog({}, default="none", state_file=state_file)
    real_replace = module.os.replace
    failed = False

    def fail_install_once(source: Path, destination: Path) -> None:
        nonlocal failed
        if not failed and Path(destination) == state_file:
            failed = True
            raise OSError("install failed")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_install_once)

    with pytest.raises(OSError, match="install failed"):
        choices.select("none")

    assert state_file.is_dir()
    assert not (tmp_path / "selection.corrupt").exists()
    restarted = OverflowCatalog({}, default="none", state_file=state_file)
    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_failed_install_and_rollback_leave_durable_recovery_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    choices = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)
    real_replace = module.os.replace

    def fail_every_primary_replace(source: Path, destination: Path) -> None:
        if Path(destination) == state_file:
            raise OSError("primary unavailable")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_every_primary_replace)

    with pytest.raises(OSError, match="primary unavailable"):
        choices.select("none")

    assert not state_file.exists()
    assert (tmp_path / "selection.corrupt").is_dir()
    assert (tmp_path / "selection.recovery").is_file()
    restarted = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)
    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_recovery_marker_blocks_default_after_an_interrupted_quarantine(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    choices = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)
    choices._ensure_recovery_marker()
    state_file.rename(tmp_path / "selection.corrupt")

    restarted = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)

    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_cleanup_of_a_moved_staging_path_cannot_fail_the_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "selection"
    choices = OverflowCatalog({}, default="none", state_file=state_file)
    real_unlink = Path.unlink

    def fail_staging_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.startswith(".selection."):
            raise OSError("cleanup failed")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staging_unlink)

    choices.select("none")

    assert state_file.read_text(encoding="utf-8").strip() == "none"


@pytest.mark.parametrize("collision", ["directory", "regular", "symlink"])
def test_unexpected_recovery_marker_is_preserved_and_owner_can_recover(
    tmp_path: Path, collision: str
) -> None:
    state_file = tmp_path / "selection"
    marker = tmp_path / "selection.recovery"
    if collision == "directory":
        marker.mkdir()
    elif collision == "regular":
        marker.write_text("unrelated data")
    else:
        target = tmp_path / "marker-target"
        target.write_text("unrelated data")
        marker.symlink_to(target)
    choices = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)

    assert choices.current.state is OverflowState.MISCONFIGURED

    choices.select("none")

    assert choices.current.state is OverflowState.OFF
    assert not module.os.path.lexists(marker)
    assert module.os.path.lexists(tmp_path / "selection.recovery.corrupt")


def test_a_removed_selection_stays_missing_until_the_owner_changes_it(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    state_file.write_text("removed\n")

    restarted = OverflowCatalog({"test": GOOD}, default="test", state_file=state_file)

    assert restarted.current.adapter_id == "removed"
    assert restarted.current.state is OverflowState.MISSING


def test_a_broken_adapter_cannot_be_selected(tmp_path: Path) -> None:
    choices = catalog(tmp_path, {"broken": BAD_CONFIG})

    with pytest.raises(ValueError, match="not selectable"):
        choices.select("broken")


def test_only_ready_adapters_and_off_are_selectable(tmp_path: Path) -> None:
    choices = catalog(
        tmp_path,
        {
            "ready": GOOD,
            "missing": "twitter_dl.adapters.gone:create",
            "broken": BAD_CONFIG,
        },
    )

    assert [choice.adapter_id for choice in choices.selectable] == ["none", "ready"]
