"""Adapter discovery and the Owner's persisted choice."""

from pathlib import Path

import pytest

from twitter_dl.services import overflow as module
from twitter_dl.services.overflow import OverflowCatalog, OverflowChoice, OverflowState

FAKES = "tests.helpers.fake_adapters"
EMPTY = "tests.helpers.no_adapters"


def catalog(tmp_path: Path, package: str = FAKES) -> OverflowCatalog:
    return OverflowCatalog(package, state_file=tmp_path / "selection")


def choice_of(source: OverflowCatalog, adapter_id: str) -> OverflowChoice:
    return next(choice for choice in source.choices if choice.adapter_id == adapter_id)


class TestDiscovery:
    def test_a_destination_subclass_in_the_package_is_a_ready_choice(self, tmp_path: Path) -> None:
        choice = choice_of(catalog(tmp_path), "test")

        assert choice.state is OverflowState.READY
        assert choice.label == "Test destination"

    def test_the_built_in_adapters_are_discovered_under_their_class_labels(
        self, tmp_path: Path
    ) -> None:
        # Their state depends on the environment (rclone, SHARE_* variables),
        # but discovery itself and the class-attribute labels do not.
        found = OverflowCatalog(state_file=tmp_path / "selection")

        assert choice_of(found, "share").label == "Share"
        assert choice_of(found, "yandex_disk").label == "Yandex Disk"

    def test_underscore_modules_are_helpers_not_adapters(self, tmp_path: Path) -> None:
        ids = [choice.adapter_id for choice in catalog(tmp_path).choices]

        assert "_helper" not in ids

    def test_an_empty_package_yields_an_empty_catalog(self, tmp_path: Path) -> None:
        assert catalog(tmp_path, EMPTY).choices == ()

    def test_a_broken_adapter_keeps_the_label_its_class_declares(self, tmp_path: Path) -> None:
        # The class imported fine; only construction failed. The Menu must
        # name the Adapter the way its author did, not guess from the file.
        choice = choice_of(catalog(tmp_path), "misconfigured")

        assert choice.state is OverflowState.MISCONFIGURED
        assert choice.label == "Broken"

    def test_an_ambiguous_module_is_named_from_its_file(self, tmp_path: Path) -> None:
        # Two classes imported, so neither label can be trusted to be "the"
        # Adapter's name — the file name is the only honest one left.
        assert choice_of(catalog(tmp_path), "ambiguous").label == "Ambiguous"

    @pytest.mark.parametrize(
        ("adapter_id", "why"),
        [
            ("misconfigured", "construction raised, like missing env settings"),
            ("needs_arguments", "the zero-argument construction contract"),
            ("duck", "no OverflowDestination subclass in the module"),
            ("synchronous", "store is not async"),
            ("wrong_signature", "store cannot take (source, *, name)"),
            ("ambiguous", "two subclasses in one module"),
            ("property_label", "label must be a class attribute"),
            ("blank_label", "label must not be blank"),
            ("crashing", "the module raised on import"),
            ("exiting", "the module raised SystemExit on import"),
            ("none", "the module name collides with Off"),
            ("packaged", "an Adapter is a single module, not a package"),
        ],
    )
    def test_every_malformed_module_is_isolated_as_misconfigured(
        self, tmp_path: Path, adapter_id: str, why: str
    ) -> None:
        choice = choice_of(catalog(tmp_path), adapter_id)

        assert choice.state is OverflowState.MISCONFIGURED, why
        assert choice.destination is None

    def test_only_ready_adapters_and_off_are_selectable(self, tmp_path: Path) -> None:
        choices = catalog(tmp_path)

        assert [choice.adapter_id for choice in choices.selectable] == ["none", "test"]

    def test_a_broken_adapter_cannot_be_selected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not selectable"):
            catalog(tmp_path).select("misconfigured")


def test_the_owner_selection_survives_a_restart(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    first = OverflowCatalog(FAKES, state_file=state_file)
    first.select("test")

    restarted = OverflowCatalog(FAKES, state_file=state_file)

    assert restarted.current.adapter_id == "test"
    assert restarted.current.ready


def test_without_a_persisted_selection_overflow_starts_off(tmp_path: Path) -> None:
    assert catalog(tmp_path).current.state is OverflowState.OFF


@pytest.mark.parametrize("broken_file", ["directory", "invalid-utf8", "empty"])
def test_an_unreadable_selection_disables_only_overflow(tmp_path: Path, broken_file: str) -> None:
    state_file = tmp_path / "selection"
    if broken_file == "directory":
        state_file.mkdir()
    elif broken_file == "empty":
        state_file.write_text("  \n")
    else:
        state_file.write_bytes(b"\xff")

    restarted = OverflowCatalog(FAKES, state_file=state_file)

    assert restarted.current.adapter_id == module.SAVED_SELECTION_ID
    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_selecting_after_a_non_file_error_quarantines_it_and_recovers(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    restarted = OverflowCatalog(FAKES, state_file=state_file)

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
    restarted = OverflowCatalog(FAKES, state_file=state_file)

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
    choices = OverflowCatalog(EMPTY, state_file=state_file)

    choices.select("none")

    assert victim.read_text(encoding="utf-8") == "keep me"
    assert state_file.is_file() and not state_file.is_symlink()


def test_staging_failure_leaves_a_non_regular_state_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    choices = OverflowCatalog(EMPTY, state_file=state_file)

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
    choices = OverflowCatalog(EMPTY, state_file=state_file)
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
    restarted = OverflowCatalog(EMPTY, state_file=state_file)
    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_failed_install_and_rollback_leave_durable_recovery_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    choices = OverflowCatalog(FAKES, state_file=state_file)
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
    restarted = OverflowCatalog(FAKES, state_file=state_file)
    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_recovery_marker_blocks_the_off_fallback_after_an_interrupted_quarantine(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "selection"
    state_file.mkdir()
    choices = OverflowCatalog(FAKES, state_file=state_file)
    choices._ensure_recovery_marker()
    state_file.rename(tmp_path / "selection.corrupt")

    restarted = OverflowCatalog(FAKES, state_file=state_file)

    assert restarted.current.state is OverflowState.MISCONFIGURED


def test_cleanup_of_a_moved_staging_path_cannot_fail_the_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "selection"
    choices = OverflowCatalog(EMPTY, state_file=state_file)
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
    choices = OverflowCatalog(FAKES, state_file=state_file)

    assert choices.current.state is OverflowState.MISCONFIGURED

    choices.select("none")

    assert choices.current.state is OverflowState.OFF
    assert not module.os.path.lexists(marker)
    assert module.os.path.lexists(tmp_path / "selection.recovery.corrupt")


def test_a_removed_selection_stays_missing_until_the_owner_changes_it(tmp_path: Path) -> None:
    state_file = tmp_path / "selection"
    state_file.write_text("removed\n")

    restarted = OverflowCatalog(FAKES, state_file=state_file)

    assert restarted.current.adapter_id == "removed"
    assert restarted.current.state is OverflowState.MISSING
