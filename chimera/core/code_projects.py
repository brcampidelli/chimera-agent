"""The projects you work in, and what you call them — kept where the agent's data is.

This list used to live only in the desktop's ``localStorage``, one browser profile at a time. That
made it a preference about a *window* rather than a fact about the installation: clearing the
webview's storage lost it, a reinstall started empty, and nothing outside that one browser could
read or seed it — so "these are my projects" was a question the backend that owns every other
durable thing here could not answer.

The alias moves with the list, and that reverses an argument this project wrote down. `projects.ts`
said an alias belongs beside the theme because it is "a preference about the interface rather than a
fact about the project". That holds right up until the list itself has to outlive the interface:
splitting them would make half the answer portable and half of it not, and the half left behind is
the half that makes a row recognisable — three of this owner's six projects are checked out under a
folder name that is not the repository's.

Two things this is **not**. It is not a claim that a registered path exists on disk: registration is
a bookmark, and a project whose folder moved should still be listed so it can be corrected rather
than silently vanishing. And it is not permission to write there — the workspace guard decides that,
from the request, and never reads this file.

One thing it now IS: the record of where the owner let the agent run commands (``shell_granted``).
That grant lived in the desktop's ``localStorage`` and reached the server only as a claim inside each
request — ``posture.reach = workspace_shell`` plus ``allow_host_exec`` — so the server believed
whatever a request said, a grant vanished when the webview's origin changed port, and the MCP bridge
and the bots could not see it at all. :func:`chimera.api.code_api.assemble_registry` reads it from
here, and a request can no longer ask for more than this file records. The grant still never beats
the owner's ``read_only`` reach nor ``CHIMERA_HOST_EXEC=deny``; those are decided further down, and
nothing in this file reaches them.

The grant matches a turn's folder by RESOLVED path, unlike the sidebar's grouping. The grouping is
about which conversations belong together, where a symlink merging two of them would be a tidier
lie; the grant is about which directory a command runs in, and that is the resolved one — a grant
recorded as ``~/loja`` must hold for the turn whose workspace arrives as ``/home/x/loja``.

Keys are the workspace string exactly as it was given, never a resolved path. Same rule the sidebar's
grouping follows and for the same reason: resolving needs a filesystem, would diverge from what the
conversations recorded, and would silently merge two projects that reach one directory through a
symlink.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

#: Enough for anyone's checkouts, and a bound on what a looping client can grow the file to.
MAX_PROJECTS = 500
MAX_PATH = 4096
MAX_ALIAS = 200


@dataclass(frozen=True, slots=True)
class RegisteredProject:
    """One bookmark: where it is, what its owner calls it, and what the owner decided about it.

    Every field after ``alias`` defaults to "nothing decided", which is also what a file written by
    an older build reads as — so an update never turns a bookmark into a grant.
    """

    path: str
    alias: str = ""
    shell_granted: bool = False
    """The owner let the agent run commands in this folder. See the module docstring."""
    granted_at: str = ""
    """When, as an ISO-8601 UTC timestamp. Empty when not granted: a revoked grant keeps no date,
    because "granted on the 3rd" under a switch that reads off describes a grant that is gone."""
    pinned: bool = False
    """Listed first in the sidebar, above the projects ordered by recency."""
    last_used_at: str = ""
    """When a coding turn last started here, ISO-8601 UTC. Empty = never seen one."""
    hidden: bool = False
    """The owner removed it from the lists. Kept as a row rather than deleted: the sidebar ALSO
    derives projects from conversations, so deleting the bookmark of a folder you have talked about
    brings it straight back — "remove" would not mean "never list"."""


class CodeProjectRegistry:
    """The registered projects, as a file under ``CHIMERA_HOME``.

    Read-modify-write under a lock, re-reading the file each time rather than caching it. The file
    is a handful of lines and the alternative — a cache — would have to answer what happens when
    something else edits it, which for a list a person may want to seed by hand is the wrong answer
    to have to give.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- reading ---------------------------------------------------------------------------

    def entries(self) -> list[RegisteredProject]:
        with self._lock:
            return self._read()

    def _read(self) -> list[RegisteredProject]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            # Keep the bytes. A project list is small and irreplaceable by the machine — the person
            # who typed it is the only copy — so a parse failure moves it aside rather than being
            # overwritten by the next add, which is what "read as empty" alone would do.
            self._set_aside()
            return []
        if not isinstance(raw, dict):
            self._set_aside()
            return []
        rows = raw.get("projects")
        if not isinstance(rows, list):
            return []
        out: list[RegisteredProject] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = row.get("path")
            alias = row.get("alias")
            if not isinstance(path, str) or not path.strip() or path in seen:
                continue
            seen.add(path)
            # `is True`, never truthiness: a hand-edited `"shell_granted": "no"` is a non-empty
            # string, and a permission must not be read out of a value nobody wrote as a yes.
            granted = row.get("shell_granted") is True
            out.append(
                RegisteredProject(
                    path,
                    alias if isinstance(alias, str) else "",
                    shell_granted=granted,
                    granted_at=_text(row.get("granted_at")) if granted else "",
                    pinned=row.get("pinned") is True,
                    last_used_at=_text(row.get("last_used_at")),
                    hidden=row.get("hidden") is True,
                )
            )
        return out

    def grants_migrated_at(self) -> str:
        """When the desktop's old browser-side grants were handed over, or "" if never."""
        with self._lock:
            return self._migrated_at()

    def _migrated_at(self) -> str:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        return _text(raw.get("grants_migrated_at")) if isinstance(raw, dict) else ""

    def shell_granted_for(self, workspace: Path | str) -> bool:
        """Whether the owner granted commands in the folder a turn will run in.

        Compared by resolved path (see the module docstring). A path that cannot be resolved grants
        nothing, which is the direction a permission may fail in.
        """
        target = _resolved(str(workspace))
        if target is None:
            return False
        return any(
            row.shell_granted and not row.hidden and _resolved(row.path) == target
            for row in self.entries()
        )

    def _set_aside(self) -> None:
        spoiled = self.path.with_suffix(self.path.suffix + ".corrupt")
        try:
            self.path.replace(spoiled)
        except OSError:  # pragma: no cover - a directory we cannot write is reported by the write
            log.warning("could not set aside the unreadable project list at %s", self.path)
        else:
            log.warning("the project list at %s could not be read; kept at %s", self.path, spoiled)

    # -- writing ---------------------------------------------------------------------------

    def register(self, path: str, alias: str | None = None) -> list[RegisteredProject]:
        """Register a project, and optionally name it.

        ``alias=None`` means *say nothing about the name* — so re-adding a project you have already
        named does not wipe the name, which is what an absent field would otherwise do. ``alias=""``
        clears it. The two read identically over HTTP unless the distinction is kept on purpose,
        which is the same one `setAlias` makes on the other side.
        """
        value = _clean(path, MAX_PATH, "path")
        name = "" if alias is None else _clean(alias, MAX_ALIAS, "alias", allow_empty=True)
        with self._lock:
            rows = self._read()
            for index, row in enumerate(rows):
                if row.path == value:
                    # `replace`, so naming a project does not quietly drop its grant or its pin.
                    rows[index] = replace(row, alias=row.alias if alias is None else name)
                    break
            else:
                if len(rows) >= MAX_PROJECTS:
                    raise ValueError(f"at most {MAX_PROJECTS} projects can be registered")
                rows.append(RegisteredProject(value, name))
            self._write(rows)
            return rows

    def remove(self, path: str) -> list[RegisteredProject]:
        """Forget a bookmark. **Conversations are not touched** — deleting those is a different act
        with its own route, and doing it here would make "tidy up my list" destroy work.

        The grant goes with the row: no record is no grant, which is the safe reading."""
        value = path.strip()
        with self._lock:
            rows = [row for row in self._read() if row.path != value]
            self._write(rows)
            return rows

    def set_grant(self, path: str, granted: bool) -> list[RegisteredProject]:
        """Grant or revoke commands in one folder, registering it if it was not.

        Registering is what makes the grant visible: a grant on a folder the lists do not show would
        be a standing permission nobody could find to take back. Granting a hidden folder unhides it
        for the same reason.
        """
        value = _clean(path, MAX_PATH, "path")
        with self._lock:
            rows = self._read()
            row = self._find_or_add(rows, value)
            if granted:
                row = replace(
                    row,
                    shell_granted=True,
                    hidden=False,
                    # Granting what is already granted keeps the date: the switch did not change.
                    granted_at=row.granted_at if row.shell_granted else _now(),
                )
            else:
                row = replace(row, shell_granted=False, granted_at="")
            self._put(rows, row)
            self._write(rows)
            return rows

    def set_flags(
        self, path: str, *, pinned: bool | None = None, hidden: bool | None = None
    ) -> list[RegisteredProject]:
        """Pin or hide one folder, registering it if it was not. None = say nothing about it.

        Hiding also REVOKES the grant and the pin. A hidden row is one the lists no longer show, and
        a permission left on where nobody can see it is the kind that never gets taken back.
        Unhiding restores neither: that is a new decision, made where it can be seen.
        """
        value = _clean(path, MAX_PATH, "path")
        with self._lock:
            rows = self._read()
            row = self._find_or_add(rows, value)
            if pinned is not None:
                row = replace(row, pinned=pinned)
            if hidden is True:
                row = replace(row, hidden=True, pinned=False, shell_granted=False, granted_at="")
            elif hidden is False:
                row = replace(row, hidden=False)
            self._put(rows, row)
            self._write(rows)
            return rows

    def touch(self, path: str) -> None:
        """Record that a turn started in this folder — only for a folder already registered.

        Not registering here, deliberately: every folder a turn ever ran in would become a row, and
        the sidebar already lists those from the conversations themselves. Never raises: a recency
        stamp is not worth failing a turn over.
        """
        value = (path or "").strip()
        if not value:
            return
        try:
            with self._lock:
                rows = self._read()
                for index, row in enumerate(rows):
                    if row.path == value:
                        rows[index] = replace(row, last_used_at=_now())
                        self._write(rows)
                        return
        except OSError:
            log.warning("could not record the last use of %s", value)

    def migrate_grants(self, paths: Iterable[str]) -> tuple[bool, int, list[RegisteredProject]]:
        """Take the desktop's old browser-side grants, ONCE per installation.

        Returns ``(migrated, recorded, rows)``. After the first call — empty or not — every later
        one changes nothing and answers ``migrated=False``: this is the single moment a client's
        word becomes a record, and leaving it open would make it a second grant route that nobody
        sees in the lists. The desktop calls it on every install, with nothing to move on a fresh
        one, precisely so the window closes.
        """
        wanted: list[str] = []
        for path in paths:
            try:
                value = _clean(path, MAX_PATH, "path")
            except ValueError:
                continue
            if value not in wanted:
                wanted.append(value)
        with self._lock:
            rows = self._read()
            if self._migrated_at():
                return False, 0, rows
            recorded = 0
            for value in wanted:
                try:
                    row = self._find_or_add(rows, value)
                except ValueError:
                    break  # the registry is full; what fitted is kept
                if not row.shell_granted:
                    recorded += 1
                    self._put(
                        rows, replace(row, shell_granted=True, hidden=False, granted_at=_now())
                    )
            self._write(rows, migrated_at=_now())
            return True, recorded, rows

    @staticmethod
    def _find_or_add(rows: list[RegisteredProject], value: str) -> RegisteredProject:
        for row in rows:
            if row.path == value:
                return row
        if len(rows) >= MAX_PROJECTS:
            raise ValueError(f"at most {MAX_PROJECTS} projects can be registered")
        row = RegisteredProject(value)
        rows.append(row)
        return row

    @staticmethod
    def _put(rows: list[RegisteredProject], row: RegisteredProject) -> None:
        for index, old in enumerate(rows):
            if old.path == row.path:
                rows[index] = row
                return

    def _write(self, rows: list[RegisteredProject], *, migrated_at: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The migration stamp survives every other write; only `migrate_grants` sets it.
        stamp = migrated_at if migrated_at is not None else self._migrated_at()
        payload: dict[str, object] = {"projects": [_row_json(r) for r in rows]}
        if stamp:
            payload["grants_migrated_at"] = stamp
        # Atomic, like every other state file here: a crash mid-write must not truncate the list
        # into something the next read has to set aside.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def _row_json(row: RegisteredProject) -> dict[str, object]:
    """A row as written: path and alias always, everything else only when set.

    So a file with no grants, pins or hidden rows is exactly what it was before these fields
    existed, and stays the short list a person can seed by hand.
    """
    out: dict[str, object] = {"path": row.path, "alias": row.alias}
    if row.shell_granted:
        out["shell_granted"] = True
        out["granted_at"] = row.granted_at
    if row.pinned:
        out["pinned"] = True
    if row.last_used_at:
        out["last_used_at"] = row.last_used_at
    if row.hidden:
        out["hidden"] = True
    return out


def _resolved(path: str) -> Path | None:
    if not path.strip():
        return None
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _clean(value: str, limit: int, field: str, *, allow_empty: bool = False) -> str:
    text = (value or "").strip()
    if not text and not allow_empty:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    return text
