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

Keys are the workspace string exactly as it was given, never a resolved path. Same rule the sidebar's
grouping follows and for the same reason: resolving needs a filesystem, would diverge from what the
conversations recorded, and would silently merge two projects that reach one directory through a
symlink.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Enough for anyone's checkouts, and a bound on what a looping client can grow the file to.
MAX_PROJECTS = 500
MAX_PATH = 4096
MAX_ALIAS = 200


@dataclass(frozen=True, slots=True)
class RegisteredProject:
    """One bookmark: where it is, and what its owner calls it."""

    path: str
    alias: str = ""


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
            out.append(RegisteredProject(path, alias if isinstance(alias, str) else ""))
        return out

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
                    rows[index] = RegisteredProject(value, row.alias if alias is None else name)
                    break
            else:
                if len(rows) >= MAX_PROJECTS:
                    raise ValueError(f"at most {MAX_PROJECTS} projects can be registered")
                rows.append(RegisteredProject(value, name))
            self._write(rows)
            return rows

    def remove(self, path: str) -> list[RegisteredProject]:
        """Forget a bookmark. **Conversations are not touched** — deleting those is a different act
        with its own route, and doing it here would make "tidy up my list" destroy work."""
        value = path.strip()
        with self._lock:
            rows = [row for row in self._read() if row.path != value]
            self._write(rows)
            return rows

    def _write(self, rows: list[RegisteredProject]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"projects": [{"path": r.path, "alias": r.alias} for r in rows]}
        # Atomic, like every other state file here: a crash mid-write must not truncate the list
        # into something the next read has to set aside.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def _clean(value: str, limit: int, field: str, *, allow_empty: bool = False) -> str:
    text = (value or "").strip()
    if not text and not allow_empty:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    return text
