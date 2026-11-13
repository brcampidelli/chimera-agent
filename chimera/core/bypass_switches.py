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

Every change of a switch that was or becomes live is recorded as a ``bypass_toggle`` entry on the
governance audit log (``audit.jsonl`` in the same home: the hash-chained file the kernel and the
releases already write, not a second log). The entry says which scope, which id, on or off, and where
the change came from (``owner_screen``, ``reactivate``, ``ttl``, ``deleted``, ``hidden``, ``pruned``).
Before this, only what the switch RELEASED was recorded, so the audit could show a run acting with no
question and never say who turned the questions off, or when.

A switch may carry an expiry (``ttl_seconds`` when it is turned on). None by default: the owner's
decisions of 27 and 28/10 made the switch per project and saved, and an expiry nobody asked for would
undo them. When one is set, every read checks it against the injected clock, and the first read past
it turns the switch off and records that with origin ``ttl``. An expiry only ever shortens a switch:
one read from the file applies to a saved entry, which still does nothing until the screen confirms
it, and a saved entry whose expiry has passed is not reactivated.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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

#: The audit file beside the switch file, the one every governance writer appends to.
AUDIT_FILE_NAME = "audit.jsonl"

#: The audit event a change of a switch writes.
TOGGLE_EVENT = "bypass_toggle"

#: Where a change came from. Only the owner's screen turns a switch ON (a toggle, or the one click
#: that reactivates what it was shown); every other origin can only turn one OFF.
ORIGIN_SCREEN = "owner_screen"
ORIGIN_REACTIVATE = "reactivate"
ORIGIN_TTL = "ttl"
ORIGIN_DELETED = "deleted"
ORIGIN_HIDDEN = "hidden"
ORIGIN_PRUNED = "pruned"

SCOPE_CONVERSATION = "conversation"
SCOPE_PROJECT = "project"

_Expiries = dict[tuple[str, str], float]


class BypassSaveError(OSError):
    """The switch could not be written to disk, so memory was left as it was."""


@dataclass(frozen=True)
class LiveSwitch:
    """One switch that is on in this process, as the security card lists it."""

    scope: str
    key: str
    expires_at: float | None = None


class BypassSwitches:
    """One home's switches. Thread-safe; every change is written before it is applied."""

    def __init__(
        self,
        path: Path | None,
        *,
        clock: Callable[[], float] = time.time,
        audit_path: Path | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self._clock = clock
        # Beside the switch file unless told otherwise. A memory-only instance (no path) audits
        # nothing unless it is handed a file: it has no home to write in.
        if audit_path is not None:
            self._audit_path: Path | None = Path(audit_path)
        elif self.path is not None:
            self._audit_path = self.path.parent / AUDIT_FILE_NAME
        else:
            self._audit_path = None
        self._lock = threading.RLock()
        self._live_conv: frozenset[str] = frozenset()
        self._live_proj: frozenset[str] = frozenset()
        self._saved_conv: frozenset[str] = frozenset()
        self._saved_proj: frozenset[str] = frozenset()
        #: ``(scope, id) -> epoch seconds`` for the switches that expire. Absent means never.
        self._expires: _Expiries = {}
        self._writing_expires: _Expiries | None = None
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
        self._expires = _clean_expires(raw.get("expires"), self._saved_conv, self._saved_proj)

    # ---- answers the rest of the server reads -------------------------------------------------

    def conversation_on(self, session_id: str) -> bool:
        with self._lock:
            self._expire_due()
            return session_id in self._live_conv

    def project_on(self, key: str) -> bool:
        with self._lock:
            self._expire_due()
            return key in self._live_proj

    def any_project_on(self) -> bool:
        with self._lock:
            self._expire_due()
            return bool(self._live_proj)

    def live_entries(self) -> list[LiveSwitch]:
        """Every switch on in this process, with its expiry: what the security card shows."""
        with self._lock:
            self._expire_due()
            rows = [
                LiveSwitch(SCOPE_PROJECT, key, self._expires.get((SCOPE_PROJECT, key)))
                for key in sorted(self._live_proj)
            ]
            rows += [
                LiveSwitch(SCOPE_CONVERSATION, key, self._expires.get((SCOPE_CONVERSATION, key)))
                for key in sorted(self._live_conv)
            ]
            return rows

    def saved_counts(self) -> tuple[int, int]:
        """(projects, conversations) saved on disk and not yet confirmed by the screen here."""
        with self._lock:
            self._expire_due()
            return (
                len(self._saved_proj - self._live_proj),
                len(self._saved_conv - self._live_conv),
            )

    # ---- changes: the screen's, each written before it is applied ----------------------------

    def saved_entries(self) -> tuple[list[str], list[str]]:
        """(project keys, conversation ids) saved on disk and not yet confirmed here, sorted: what the
        banner lists before the one click, and exactly what that click may confirm."""
        with self._lock:
            self._expire_due()
            return (
                sorted(self._saved_proj - self._live_proj),
                sorted(self._saved_conv - self._live_conv),
            )

    def set_conversation(
        self,
        session_id: str,
        enabled: bool,
        *,
        origin: str = ORIGIN_SCREEN,
        ttl_seconds: float | None = None,
    ) -> str | None:
        """Returns a warning when turning OFF could not be saved (see the module docstring).

        ``ttl_seconds`` applies only when turning ON: the switch then goes off by itself that many
        seconds later. None, the default, keeps it on until someone turns it off.
        """
        _only_the_screen_turns_on(enabled, origin)
        with self._lock:
            self._expire_due()
            live = set(self._live_conv)
            saved = set(self._saved_conv)
            if enabled:
                live.add(session_id)
            else:
                live.discard(session_id)
            # A screen toggle confirms that one entry: it leaves the saved set either way.
            saved.discard(session_id)
            expires = self._expiry_after(SCOPE_CONVERSATION, session_id, enabled, ttl_seconds)
            warning = self._change(
                live, saved, set(self._live_proj), set(self._saved_proj),
                enabled=enabled, expires=expires,
            )
            self._record(SCOPE_CONVERSATION, session_id, enabled, origin)
            return warning

    def set_project(
        self,
        key: str,
        enabled: bool,
        *,
        origin: str = ORIGIN_SCREEN,
        ttl_seconds: float | None = None,
    ) -> str | None:
        """Returns a warning when turning OFF could not be saved (see the module docstring)."""
        _only_the_screen_turns_on(enabled, origin)
        with self._lock:
            self._expire_due()
            live = set(self._live_proj)
            saved = set(self._saved_proj)
            if enabled:
                live.add(key)
            else:
                live.discard(key)
            saved.discard(key)
            expires = self._expiry_after(SCOPE_PROJECT, key, enabled, ttl_seconds)
            warning = self._change(
                set(self._live_conv), set(self._saved_conv), live, saved,
                enabled=enabled, expires=expires,
            )
            self._record(SCOPE_PROJECT, key, enabled, origin)
            return warning

    def reactivate(self, projects: Iterable[str], conversations: Iterable[str]) -> tuple[int, int]:
        """The one click, for the entries the owner was SHOWN: each listed id or folder that is saved
        becomes live. Anything saved and not listed is dropped from the file, because the owner did not
        recognise it. An empty pair therefore discards every saved switch (the banner's "dismiss").

        Ids that are not saved are ignored, so a request cannot turn on a switch the disk never held.
        A saved entry whose expiry has already passed is not reactivated: it would hold for no time,
        and the owner would have confirmed a switch that never held. Returns how many (projects,
        conversations) became live.
        """
        with self._lock:
            now = self._clock()
            proj_on = {
                key for key in set(projects) & self._saved_proj
                if not _expired(self._expires.get((SCOPE_PROJECT, key)), now)
            }
            conv_on = {
                key for key in set(conversations) & self._saved_conv
                if not _expired(self._expires.get((SCOPE_CONVERSATION, key)), now)
            }
            self._commit(
                set(self._live_conv) | conv_on, set(),
                set(self._live_proj) | proj_on, set(),
            )
            if proj_on or conv_on:
                self.restored_ok = True
            for key in sorted(proj_on):
                self._record(SCOPE_PROJECT, key, True, ORIGIN_REACTIVATE)
            for key in sorted(conv_on):
                self._record(SCOPE_CONVERSATION, key, True, ORIGIN_REACTIVATE)
            return len(proj_on), len(conv_on)

    def forget_conversation(self, session_id: str) -> None:
        """The conversation was deleted: its switch goes with it. Best effort on the file; the
        memory always forgets, and a stale id left on disk is pruned at the next start."""
        self._forget(conversation=session_id, origin=ORIGIN_DELETED)

    def forget_project(self, key: str) -> None:
        """The folder was hidden: its switch is revoked. Best effort on the file, as above."""
        self._forget(project=key, origin=ORIGIN_HIDDEN)

    def prune_conversations(self, exists: Callable[[str], bool]) -> None:
        """Drop the switches of conversations that no longer exist (checked at start)."""
        with self._lock:
            gone_live = {c for c in self._live_conv if not exists(c)}
            gone_saved = {c for c in self._saved_conv if not exists(c)}
            if not gone_live and not gone_saved:
                return
            self._forget_many(conversations=gone_live | gone_saved, origin=ORIGIN_PRUNED)

    # ---- internals ---------------------------------------------------------------------------

    def _forget(self, *, conversation: str | None = None, project: str | None = None, origin: str) -> None:
        with self._lock:
            conv = {conversation} if conversation else set()
            proj = {project} if project else set()
            self._forget_many(conversations=conv, projects=proj, origin=origin)

    def _forget_many(
        self, *, conversations: Iterable[str] = (), projects: Iterable[str] = (), origin: str,
    ) -> None:
        conv_gone = set(conversations)
        proj_gone = set(projects)
        # Only a switch that was ON in this process is recorded as turned off: a saved one did
        # nothing, and an "off" entry for a switch that never held would be noise on the chain.
        was_on = [(SCOPE_CONVERSATION, key) for key in sorted(conv_gone & self._live_conv)]
        was_on += [(SCOPE_PROJECT, key) for key in sorted(proj_gone & self._live_proj)]
        live_c = set(self._live_conv) - conv_gone
        saved_c = set(self._saved_conv) - conv_gone
        live_p = set(self._live_proj) - proj_gone
        saved_p = set(self._saved_proj) - proj_gone
        try:
            self._commit(live_c, saved_c, live_p, saved_p)
        except BypassSaveError as exc:
            # The file keeps the id until the next write or start; memory must not keep it on.
            _log.warning("could not rewrite the switches after a delete: %s", exc)
            self._apply(live_c, saved_c, live_p, saved_p)
        for scope, key in was_on:
            self._record(scope, key, False, origin)

    def _expiry_after(self, scope: str, key: str, enabled: bool, ttl_seconds: float | None) -> _Expiries:
        """The expiry map once this change is made: set for an ON with a TTL, dropped otherwise."""
        expires = dict(self._expires)
        expires.pop((scope, key), None)
        if enabled and ttl_seconds is not None:
            if ttl_seconds <= 0:
                raise ValueError("ttl_seconds must be positive")
            expires[(scope, key)] = self._clock() + float(ttl_seconds)
        return expires

    def _expire_due(self) -> None:
        """Turn off every live switch whose expiry has passed, and record each with origin ``ttl``.

        Called with the lock held at the top of every read, so no reader sees a switch on past its
        time: the expiry is a fact about the clock, not about whether something happened to poll it.
        """
        if not self._expires:
            return
        now = self._clock()
        due = sorted(
            (scope, key) for (scope, key), at in self._expires.items()
            if _expired(at, now)
            and key in (self._live_conv if scope == SCOPE_CONVERSATION else self._live_proj)
        )
        if not due:
            return
        live_c = set(self._live_conv) - {key for scope, key in due if scope == SCOPE_CONVERSATION}
        live_p = set(self._live_proj) - {key for scope, key in due if scope == SCOPE_PROJECT}
        expires = {entry: at for entry, at in self._expires.items() if entry not in set(due)}
        # Off first and written after, like any switch turned off: the expiry must hold in this
        # process whatever the disk does.
        self._apply(live_c, set(self._saved_conv), live_p, set(self._saved_proj), expires)
        try:
            self._write(live_c | set(self._saved_conv), live_p | set(self._saved_proj))
        except BypassSaveError as exc:
            _log.warning("a switch expired, but the file could not be rewritten: %s", exc)
        for scope, key in due:
            self._record(scope, key, False, ORIGIN_TTL)

    def _record(self, scope: str, key: str, enabled: bool, origin: str) -> None:
        """One ``bypass_toggle`` entry on the audit chain.

        Best effort, and after the change: a switch the owner turned off must stay off even when the
        audit cannot be written. The failure is logged, never raised.
        """
        if self._audit_path is None:
            return
        from chimera.governance.audit import AuditLog

        payload: dict[str, object] = {"scope": scope, "id": key, "enabled": enabled, "origin": origin}
        expires_at = self._expires.get((scope, key)) if enabled else None
        if expires_at is not None:
            payload["expires_at"] = expires_at
        try:
            AuditLog(self._audit_path).record(TOGGLE_EVENT, payload)
        except OSError as exc:
            _log.warning("could not record the switch change in the audit log: %s", exc)

    def _commit(
        self,
        live_c: set[str],
        saved_c: set[str],
        live_p: set[str],
        saved_p: set[str],
        expires: _Expiries | None = None,
    ) -> None:
        """Write the whole state (live and saved together), then, and only then, apply it."""
        kept = _kept_expires(self._expires if expires is None else expires, live_c | saved_c, live_p | saved_p)
        # Handed to `_write` beside its two arguments rather than as a third: the write runs BEFORE
        # memory changes, so it cannot read the new expiries off `self._expires`.
        self._writing_expires = kept
        try:
            self._write(live_c | saved_c, live_p | saved_p)
        finally:
            self._writing_expires = None
        self._apply(live_c, saved_c, live_p, saved_p, kept)

    def _change(
        self,
        live_c: set[str],
        saved_c: set[str],
        live_p: set[str],
        saved_p: set[str],
        *,
        enabled: bool,
        expires: _Expiries | None = None,
    ) -> str | None:
        """One screen change: a switch turned ON is written first (and raises with memory unchanged);
        one turned OFF is applied first and written after, so a failed write cannot leave it on."""
        if enabled:
            self._commit(live_c, saved_c, live_p, saved_p, expires)
            return None
        self._apply(live_c, saved_c, live_p, saved_p, expires)
        try:
            self._write(live_c | saved_c, live_p | saved_p)
        except BypassSaveError as exc:
            _log.warning("switch turned off in memory, but the file could not be rewritten: %s", exc)
            return OFF_NOT_SAVED
        return None

    def _apply(
        self,
        live_c: set[str],
        saved_c: set[str],
        live_p: set[str],
        saved_p: set[str],
        expires: _Expiries | None = None,
    ) -> None:
        self._live_conv, self._saved_conv = frozenset(live_c), frozenset(saved_c)
        self._live_proj, self._saved_proj = frozenset(live_p), frozenset(saved_p)
        self._expires = _kept_expires(
            self._expires if expires is None else expires, live_c | saved_c, live_p | saved_p,
        )

    def _write(self, conversations: set[str], projects: set[str]) -> None:
        if self.path is None:
            return
        pending = self._writing_expires
        kept = _kept_expires(self._expires if pending is None else pending, conversations, projects)
        payload: dict[str, object] = {"conversations": sorted(conversations), "projects": sorted(projects)}
        if kept:
            # Only when some switch expires, so a file with none is what it always was.
            payload["expires"] = {f"{scope}:{key}": at for (scope, key), at in sorted(kept.items())}
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


def _clean_expires(value: object, conversations: frozenset[str], projects: frozenset[str]) -> _Expiries:
    """The expiries the file lists for entries it also lists. Anything else is dropped: an expiry
    only shortens a switch, so a malformed one is read as absent, never as something wider."""
    if not isinstance(value, dict):
        return {}
    out: _Expiries = {}
    for raw_key, at in value.items():
        if not isinstance(raw_key, str) or isinstance(at, bool) or not isinstance(at, (int, float)):
            continue
        scope, _, key = raw_key.partition(":")
        if (scope == SCOPE_CONVERSATION and key in conversations) or (scope == SCOPE_PROJECT and key in projects):
            out[(scope, key)] = float(at)
    return out


def _kept_expires(expires: _Expiries, conversations: Iterable[str], projects: Iterable[str]) -> _Expiries:
    """The expiries of the entries that still exist."""
    conv, proj = set(conversations), set(projects)
    return {
        (scope, key): at for (scope, key), at in expires.items()
        if (scope == SCOPE_CONVERSATION and key in conv) or (scope == SCOPE_PROJECT and key in proj)
    }


def _only_the_screen_turns_on(enabled: bool, origin: str) -> None:
    """Refuse an ON from any origin but the owner's screen, before anything is written or recorded.

    The routes already refuse every other caller; this keeps the module from being the one place a
    new caller could switch the bypass on by naming another origin.
    """
    if enabled and origin != ORIGIN_SCREEN:
        raise PermissionError(f"only the owner's screen turns this switch on (origin {origin!r})")


def _expired(at: float | None, now: float) -> bool:
    return at is not None and now >= at


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
