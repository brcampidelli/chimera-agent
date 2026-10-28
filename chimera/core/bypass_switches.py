"""The owner's "Ignore permissions" switches: saved across restarts, never trusted from the disk.

Two kinds of switch are kept, per conversation and per project folder. Each has two sets:

- ``live``: switched on in this process, by the owner's screen (or by its one-click reactivation);
- ``saved``: read from the file when the server started, and NOT yet confirmed by the screen here.

Only ``live`` counts. A saved switch does nothing until the screen confirms it, once, in this
process (:meth:`BypassSwitches.reactivate` for the entries the owner was shown, or a screen toggle
for one). The file is read exactly once, when it is opened; a file rewritten during the session is never read
again, so an agent that writes it cannot switch anything on. What the owner approved is the memory,
and the file is only a copy of memory (live and saved together) written after each change.

Writes go to the file first, for a switch turned ON: if the file cannot be written the memory is not
changed, and the caller gets :class:`BypassSaveError`. A switch turned OFF is the other way round: the
memory goes off at once (the owner's "off" must hold in this process whatever the disk does), and a
failed write is logged and reported as a warning. Nothing turns on from a file that failed to say so:
a switch the disk still lists is only ever a saved one, and saved does nothing.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from chimera.telemetry import get_logger

_log = get_logger("core.bypass_switches")

#: What a switch that is off in memory but could not be written says to the owner.
OFF_NOT_SAVED = (
    "The switch is off in this process, but saving that to disk failed. The next start may show it "
    "as saved again; a saved switch does nothing until the screen confirms it."
)

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

    def saved_entries(self) -> tuple[list[str], list[str]]:
        """(project keys, conversation ids) saved on disk and not yet confirmed here, sorted: what the
        banner lists before the one click, and exactly what that click may confirm."""
        with self._lock:
            return (
                sorted(self._saved_proj - self._live_proj),
                sorted(self._saved_conv - self._live_conv),
            )

    def set_conversation(self, session_id: str, enabled: bool) -> str | None:
        """Returns a warning when turning OFF could not be saved (see the module docstring)."""
        with self._lock:
            live = set(self._live_conv)
            saved = set(self._saved_conv)
            if enabled:
                live.add(session_id)
            else:
                live.discard(session_id)
            # A screen toggle confirms that one entry: it leaves the saved set either way.
            saved.discard(session_id)
            return self._change(live, saved, set(self._live_proj), set(self._saved_proj), enabled=enabled)

    def set_project(self, key: str, enabled: bool) -> str | None:
        """Returns a warning when turning OFF could not be saved (see the module docstring)."""
        with self._lock:
            live = set(self._live_proj)
            saved = set(self._saved_proj)
            if enabled:
                live.add(key)
            else:
                live.discard(key)
            saved.discard(key)
            return self._change(set(self._live_conv), set(self._saved_conv), live, saved, enabled=enabled)

    def reactivate(self, projects: Iterable[str], conversations: Iterable[str]) -> tuple[int, int]:
        """The one click, for the entries the owner was SHOWN: each listed id or folder that is saved
        becomes live. Anything saved and not listed is dropped from the file, because the owner did not
        recognise it. An empty pair therefore discards every saved switch (the banner's "dismiss").

        Ids that are not saved are ignored, so a request cannot turn on a switch the disk never held.
        Returns how many (projects, conversations) became live.
        """
        with self._lock:
            proj_on = set(projects) & self._saved_proj
            conv_on = set(conversations) & self._saved_conv
            self._commit(
                set(self._live_conv) | conv_on, set(),
                set(self._live_proj) | proj_on, set(),
            )
            if proj_on or conv_on:
                self.restored_ok = True
            return len(proj_on), len(conv_on)

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
        self._apply(live_c, saved_c, live_p, saved_p)

    def _change(
        self,
        live_c: set[str],
        saved_c: set[str],
        live_p: set[str],
        saved_p: set[str],
        *,
        enabled: bool,
    ) -> str | None:
        """One screen change: a switch turned ON is written first (and raises with memory unchanged);
        one turned OFF is applied first and written after, so a failed write cannot leave it on."""
        if enabled:
            self._commit(live_c, saved_c, live_p, saved_p)
            return None
        self._apply(live_c, saved_c, live_p, saved_p)
        try:
            self._write(live_c | saved_c, live_p | saved_p)
        except BypassSaveError as exc:
            _log.warning("switch turned off in memory, but the file could not be rewritten: %s", exc)
            return OFF_NOT_SAVED
        return None

    def _apply(self, live_c: set[str], saved_c: set[str], live_p: set[str], saved_p: set[str]) -> None:
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
