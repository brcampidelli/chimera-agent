"""The owner's "Ignore permissions" switches: saved across restarts, never trusted from the disk.

Two kinds of switch are kept, per conversation and per project folder. Each has two sets:

- ``live``: switched on in this process, by the owner's screen (or by its one-click reactivation);
- ``saved``: read from the file when the server started, and NOT yet confirmed by the screen here.

Only ``live`` counts. A saved switch does nothing until the screen confirms it, once, in this
process (:meth:`BypassSwitches.reactivate_saved` for all of them, or a screen toggle for one). The
file is read exactly once, when it is opened; a file rewritten during the session is never read
again, so an agent that writes it cannot switch anything on. What the owner approved is the memory,
and the file is only a copy of memory (live and saved together) written after each change.

Writes go to the file first. If the file cannot be written the memory is not changed, and the caller
gets :class:`BypassSaveError`, so what the screen shows is what the next start will read back.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from chimera.telemetry import get_logger

_log = get_logger("core.bypass_switches")

#: The file name in the app's home. Written only from memory; see the module docstring.
SWITCH_FILE_NAME = "code_bypass_switches.json"


class BypassSaveError(OSError):
    """The switch could not be written to disk, so memory was left as it was."""


class BypassSwitches:
    """One home's switches. Thread-safe; every change is written before it is applied."""

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._live_conv: frozenset[str] = frozenset()
        self._live_proj: frozenset[str] = frozenset()
        self._saved_conv: frozenset[str] = frozenset()
        self._saved_proj: frozenset[str] = frozenset()
        self.restored_ok = False
        """Set once the screen has reactivated the saved switches in this process."""
        self._read_saved()

    # ---- reading (never after the first load) --------------------------------------------------

    def _read_saved(self) -> None:
        if self.path is None:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self._saved_conv = _clean_ids(raw.get("conversations"))
        self._saved_proj = _clean_ids(raw.get("projects"))

    # ---- answers the rest of the server reads -------------------------------------------------

    def conversation_on(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._live_conv

    def project_on(self, key: str) -> bool:
        with self._lock:
            return key in self._live_proj

    def any_project_on(self) -> bool:
        with self._lock:
            return bool(self._live_proj)

    def saved_counts(self) -> tuple[int, int]:
        """(projects, conversations) saved on disk and not yet confirmed by the screen here."""
        with self._lock:
            return (
                len(self._saved_proj - self._live_proj),
                len(self._saved_conv - self._live_conv),
            )

    # ---- changes: the screen's, each written before it is applied ----------------------------

    def set_conversation(self, session_id: str, enabled: bool) -> None:
        with self._lock:
            live = set(self._live_conv)
            saved = set(self._saved_conv)
            if enabled:
                live.add(session_id)
            else:
                live.discard(session_id)
            # A screen toggle confirms that one entry: it leaves the saved set either way.
            saved.discard(session_id)
            self._commit(live, saved, set(self._live_proj), set(self._saved_proj))

    def set_project(self, key: str, enabled: bool) -> None:
        with self._lock:
            live = set(self._live_proj)
            saved = set(self._saved_proj)
            if enabled:
                live.add(key)
            else:
                live.discard(key)
            saved.discard(key)
            self._commit(set(self._live_conv), set(self._saved_conv), live, saved)

    def reactivate_saved(self) -> tuple[int, int]:
        """The one click: every saved switch becomes live. Returns how many (projects, conversations)."""
        with self._lock:
            projects = len(self._saved_proj - self._live_proj)
            conversations = len(self._saved_conv - self._live_conv)
            self._commit(
                set(self._live_conv) | set(self._saved_conv), set(),
                set(self._live_proj) | set(self._saved_proj), set(),
            )
            self.restored_ok = True
            return projects, conversations

    def forget_conversation(self, session_id: str) -> None:
        """The conversation was deleted: its switch goes with it. Best effort on the file; the
        memory always forgets, and a stale id left on disk is pruned at the next start."""
        self._forget(conversation=session_id)

    def forget_project(self, key: str) -> None:
        """The folder was hidden: its switch is revoked. Best effort on the file, as above."""
        self._forget(project=key)

    def prune_conversations(self, exists: Callable[[str], bool]) -> None:
        """Drop the switches of conversations that no longer exist (checked at start)."""
        with self._lock:
            gone_live = {c for c in self._live_conv if not exists(c)}
            gone_saved = {c for c in self._saved_conv if not exists(c)}
            if not gone_live and not gone_saved:
                return
            self._forget_many(conversations=gone_live | gone_saved)

    # ---- internals ---------------------------------------------------------------------------

    def _forget(self, *, conversation: str | None = None, project: str | None = None) -> None:
        with self._lock:
            conv = {conversation} if conversation else set()
            proj = {project} if project else set()
            self._forget_many(conversations=conv, projects=proj)

    def _forget_many(self, *, conversations: Iterable[str] = (), projects: Iterable[str] = ()) -> None:
        conv_gone = set(conversations)
        proj_gone = set(projects)
        live_c = set(self._live_conv) - conv_gone
        saved_c = set(self._saved_conv) - conv_gone
        live_p = set(self._live_proj) - proj_gone
        saved_p = set(self._saved_proj) - proj_gone
        try:
            self._commit(live_c, saved_c, live_p, saved_p)
        except BypassSaveError as exc:
            # The file keeps the id until the next write or start; memory must not keep it on.
            _log.warning("could not rewrite the switches after a delete: %s", exc)
            self._live_conv, self._saved_conv = frozenset(live_c), frozenset(saved_c)
            self._live_proj, self._saved_proj = frozenset(live_p), frozenset(saved_p)

    def _commit(
        self,
        live_c: set[str],
        saved_c: set[str],
        live_p: set[str],
        saved_p: set[str],
    ) -> None:
        """Write the whole state (live and saved together), then, and only then, apply it."""
        self._write(live_c | saved_c, live_p | saved_p)
        self._live_conv, self._saved_conv = frozenset(live_c), frozenset(saved_c)
        self._live_proj, self._saved_proj = frozenset(live_p), frozenset(saved_p)

    def _write(self, conversations: set[str], projects: set[str]) -> None:
        if self.path is None:
            return
        payload = {"conversations": sorted(conversations), "projects": sorted(projects)}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            raise BypassSaveError(f"could not save the permission switches: {exc}") from exc


def _clean_ids(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str) and item.strip())


_BY_PATH: dict[str, BypassSwitches] = {}
_REGISTRY_LOCK = threading.RLock()


def open_switches(path: Path | None) -> BypassSwitches:
    """The switches of one home, read once and then shared by every reader in this process."""
    if path is None:
        return BypassSwitches(None)
    key = str(Path(path))
    with _REGISTRY_LOCK:
        found = _BY_PATH.get(key)
        if found is None:
            found = BypassSwitches(Path(path))
            _BY_PATH[key] = found
        return found


def forget_all_switches() -> None:
    """Drop every cached home. A process restart, for tests: the next open reads the file again."""
    with _REGISTRY_LOCK:
        _BY_PATH.clear()
