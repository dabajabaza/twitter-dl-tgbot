"""Optional destinations for clips that do not fit in Telegram."""

import importlib
import inspect
import logging
import os
import re
import stat
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

_ADAPTER_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")
_OFF_ID = "none"
SAVED_SELECTION_ID = "!saved-selection"
_RECOVERY_MARKER = "twitter-dl overflow selection recovery v1\n"


class OverflowDestination(ABC):
    """Store one oversized clip and return a locator suitable for the chat."""

    @property
    @abstractmethod
    def label(self) -> str: ...

    @abstractmethod
    async def store(self, source: Path, *, name: str) -> str: ...


class OverflowState(StrEnum):
    OFF = "off"
    READY = "ready"
    MISSING = "missing"
    MISCONFIGURED = "misconfigured"


@dataclass(frozen=True)
class OverflowChoice:
    """One configured Menu choice, including a failure to construct it."""

    adapter_id: str
    label: str
    state: OverflowState
    destination: OverflowDestination | None = None

    @property
    def ready(self) -> bool:
        return self.state is OverflowState.READY


_OFF = OverflowChoice(adapter_id=_OFF_ID, label=_OFF_ID, state=OverflowState.OFF)


class OverflowCatalog:
    """Loaded Adapter choices and the Owner's persisted selection."""

    def __init__(
        self,
        factories: Mapping[str, object],
        *,
        default: str,
        state_file: Path,
    ) -> None:
        self._choices = {
            adapter_id: _load(adapter_id, factory_path)
            for raw_id, factory_path in factories.items()
            if (adapter_id := raw_id.strip().lower())
        }
        self._default = default.strip().lower() or _OFF_ID
        self._state_file = state_file
        self._recovery_file = state_file.with_name(f"{state_file.name}.recovery")
        self._selection_error: str | None = None
        self._selected_id = self._read_selection()
        if (
            self._selection_error is None
            and self._selected_id != _OFF_ID
            and self._selected_id not in self._choices
        ):
            logger.error("selected overflow adapter %s is missing", self._selected_id)

    @property
    def choices(self) -> tuple[OverflowChoice, ...]:
        return tuple(self._choices.values())

    @property
    def selectable(self) -> tuple[OverflowChoice, ...]:
        return (_OFF, *(choice for choice in self.choices if choice.ready))

    @property
    def current(self) -> OverflowChoice:
        if self._selection_error is not None:
            return OverflowChoice(
                adapter_id=SAVED_SELECTION_ID,
                label=SAVED_SELECTION_ID,
                state=OverflowState.MISCONFIGURED,
            )
        if self._selected_id == _OFF_ID:
            return _OFF
        choice = self._choices.get(self._selected_id)
        if choice is not None:
            return choice
        return OverflowChoice(
            adapter_id=self._selected_id,
            label=_label_from_id(self._selected_id),
            state=OverflowState.MISSING,
        )

    def select(self, adapter_id: str) -> OverflowChoice:
        """Persist a working choice, then make it active for new Requests."""
        normalized = adapter_id.strip().lower()
        choice = _OFF if normalized == _OFF_ID else self._choices.get(normalized)
        if choice is None or (choice.state is not OverflowState.OFF and not choice.ready):
            raise ValueError(f"overflow adapter {adapter_id!r} is not selectable")
        self._write_selection(normalized)
        self._selected_id = normalized
        self._selection_error = None
        return choice

    def _read_selection(self) -> str:
        if os.path.lexists(self._recovery_file):
            self._selection_error = "saved selection recovery is incomplete"
            logger.error("%s: %s", self._recovery_file, self._selection_error)
            return SAVED_SELECTION_ID
        if os.path.lexists(self._state_file) and not self._state_is_regular():
            self._selection_error = "saved selection is not a regular file"
            logger.error("%s: %s", self._state_file, self._selection_error)
            return SAVED_SELECTION_ID
        try:
            selected = self._state_file.read_text(encoding="utf-8").strip().lower()
        except FileNotFoundError:
            if os.path.lexists(self._state_file):
                self._selection_error = "saved selection points to a missing file"
                logger.error("%s: %s", self._state_file, self._selection_error)
                return SAVED_SELECTION_ID
            return self._default
        except (OSError, UnicodeError) as exc:
            logger.error("could not read overflow selection %s: %s", self._state_file, exc)
            self._selection_error = str(exc)
            return SAVED_SELECTION_ID
        if not selected:
            self._selection_error = "saved selection is empty"
            logger.error("%s: %s", self._state_file, self._selection_error)
            return SAVED_SELECTION_ID
        return selected

    def _write_selection(self, adapter_id: str) -> None:
        temporary = self._stage(f"{adapter_id}\n")
        installed = False
        recovering = os.path.lexists(self._recovery_file) or (
            os.path.lexists(self._state_file) and not self._state_is_regular()
        )
        try:
            if recovering:
                self._ensure_recovery_marker()
            quarantine = self._quarantine_non_file()
            try:
                os.replace(temporary, self._state_file)
            except BaseException:
                if quarantine is not None:
                    try:
                        os.replace(quarantine, self._state_file)
                    except OSError as rollback_error:
                        logger.critical(
                            "could not restore overflow selection %s: %s",
                            self._state_file,
                            rollback_error,
                        )
                    else:
                        self._discard_recovery_marker()
                raise
            installed = True
            if recovering:
                self._clear_recovery_marker()
        finally:
            if not installed:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning("could not remove staged overflow selection: %s", cleanup_error)

    def _stage(self, content: str) -> Path:
        staged: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._state_file.parent,
                prefix=f".{self._state_file.name}.",
                delete=False,
            ) as handle:
                staged = Path(handle.name)
                handle.write(content)
            return staged
        except BaseException:
            if staged is not None:
                try:
                    staged.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning("could not remove incomplete staged state: %s", cleanup_error)
            raise

    def _ensure_recovery_marker(self) -> None:
        if os.path.lexists(self._recovery_file):
            return
        marker = self._stage(_RECOVERY_MARKER)
        installed = False
        try:
            os.replace(marker, self._recovery_file)
            installed = True
        finally:
            if not installed:
                try:
                    marker.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning("could not remove staged recovery marker: %s", cleanup_error)

    def _discard_recovery_marker(self) -> None:
        if os.path.lexists(self._recovery_file) and not self._recovery_marker_is_owned():
            return
        try:
            self._recovery_file.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logger.warning("could not remove overflow recovery marker: %s", cleanup_error)

    def _clear_recovery_marker(self) -> None:
        if not os.path.lexists(self._recovery_file):
            return
        if self._recovery_marker_is_owned():
            self._recovery_file.unlink()
            return
        quarantine = self._quarantine(self._recovery_file)
        logger.warning("moved unexpected overflow recovery marker to %s", quarantine)

    def _recovery_marker_is_owned(self) -> bool:
        if not self._path_is_regular(self._recovery_file):
            return False
        try:
            return self._recovery_file.read_text(encoding="utf-8") == _RECOVERY_MARKER
        except (OSError, UnicodeError):
            return False

    def _quarantine_non_file(self) -> Path | None:
        if not os.path.lexists(self._state_file) or self._state_is_regular():
            return None
        return self._quarantine(self._state_file)

    @staticmethod
    def _quarantine(path: Path) -> Path:
        index = 0
        while True:
            suffix = ".corrupt" if index == 0 else f".corrupt-{index}"
            quarantine = path.with_name(f"{path.name}{suffix}")
            if not os.path.lexists(quarantine):
                path.rename(quarantine)
                logger.warning("moved invalid path to %s", quarantine)
                return quarantine
            index += 1

    def _state_is_regular(self) -> bool:
        return self._path_is_regular(self._state_file)

    @staticmethod
    def _path_is_regular(path: Path) -> bool:
        try:
            return stat.S_ISREG(path.lstat().st_mode)
        except OSError:
            return False


def _load(adapter_id: str, factory_path: object) -> OverflowChoice:
    label = _label_from_id(adapter_id)
    if adapter_id == _OFF_ID:
        return _broken(
            adapter_id,
            label,
            OverflowState.MISCONFIGURED,
            "adapter id 'none' is reserved for disabled Overflow delivery",
        )
    if not _ADAPTER_ID.fullmatch(adapter_id):
        return _broken(
            adapter_id,
            label,
            OverflowState.MISCONFIGURED,
            "adapter id must match [a-z][a-z0-9_-]{0,47}",
        )

    if not isinstance(factory_path, str):
        return _broken(
            adapter_id,
            label,
            OverflowState.MISCONFIGURED,
            "factory path must be a string",
        )
    module_name, separator, factory_name = factory_path.strip().partition(":")
    if not separator or not module_name or not factory_name:
        return _broken(
            adapter_id,
            label,
            OverflowState.MISCONFIGURED,
            "factory path must have the form python.module:create",
        )

    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        state = (
            OverflowState.MISSING
            if exc.name == module_name or module_name.startswith(f"{exc.name}.")
            else OverflowState.MISCONFIGURED
        )
        return _broken(adapter_id, label, state, str(exc))
    except (Exception, SystemExit) as exc:
        return _broken(adapter_id, label, OverflowState.MISCONFIGURED, str(exc))

    try:
        factory = getattr(module, factory_name, None)
        if not callable(factory):
            return _broken(
                adapter_id,
                label,
                OverflowState.MISSING,
                f"{factory_path} is not a callable factory",
            )
        destination = cast(Callable[[], object], factory)()
        if not isinstance(destination, OverflowDestination):
            raise TypeError("factory did not return an OverflowDestination")
        destination_label = destination.label
        if not isinstance(destination_label, str) or not destination_label.strip():
            raise ValueError("adapter label must not be empty")
        if not inspect.iscoroutinefunction(destination.store):
            raise TypeError("OverflowDestination.store must be async")
        signature = inspect.signature(destination.store)
        signature.bind(Path("clip"), name="clip.mp4")
        name_parameter = signature.parameters.get("name")
        if name_parameter is None or name_parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            raise TypeError("OverflowDestination.store name must be keyword-only")
    except (Exception, SystemExit) as exc:
        return _broken(adapter_id, label, OverflowState.MISCONFIGURED, str(exc))

    return OverflowChoice(
        adapter_id=adapter_id,
        label=destination_label,
        state=OverflowState.READY,
        destination=destination,
    )


def _broken(adapter_id: str, label: str, state: OverflowState, error: str) -> OverflowChoice:
    logger.error("overflow adapter %s is %s: %s", adapter_id, state.value, error)
    return OverflowChoice(
        adapter_id=adapter_id,
        label=label,
        state=state,
    )


def _label_from_id(adapter_id: str) -> str:
    return adapter_id.replace("_", " ").replace("-", " ").title()
