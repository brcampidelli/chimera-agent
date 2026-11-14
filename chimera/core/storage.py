"""What this install keeps on disk, by kind — and the two things that may safely be let go.

Study 29, P5.3. Nothing measured disk until now: `chimera/core/resources.py` reads CPU, memory and
VRAM, while the isolated worktrees — each a full checkout of the repository — went to the system
temp folder, on the same drive that has already filled once on this machine.

The rule is the one `resources.py` states: **a measurement that could not be taken is reported as
absent, never as zero.** A category reads ``bytes: null`` when its folder could not be read, when
counting it ran past its time budget, or when its location is unknown here; ``0`` means it was
counted and nothing was there. A partial count is not a measurement either: a lower bound shown as
a size is believed as one.

The worktrees are read one by one with their age and WHY each still exists (:func:`_retention`), and
the report says when their total passes the owner's limit and which idle conversations could be
ARCHIVED to let the existing thirty-day clean-up (`worktree_expiry`) do the rest. This module only
reads for that: it never removes a worktree and never archives — the archive is the owner's click on
the conversation list's own route.

What may be removed from here is deliberately short: orphaned worktrees
(:func:`chimera.core.worktree.prune_worktree_dirs`), one rotation of the diagnostic traces
(:func:`rotate_logs`) and the response cache folder (:func:`clear_cache`). Sessions, memory, history, approvals and schedules are measured and never
deleted by this module — their loss is not recoverable, and a storage screen is the wrong place to
discover that.
"""

from __future__ import annotations

import copy
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.storage")

#: The clock every deadline here is read on. ``time.monotonic`` — named, so a test can drive the
#: report's time limit by a clock it advances itself instead of by real sleeps. On Windows before
#: Python 3.13 ``time.monotonic`` is ``GetTickCount64``, which moves in 15.625 ms steps; a test that
#: sleeps "until the deadline" lands on that grid and its result depended on which side of a tick
#: the sleep woke.
clock: Callable[[], float] = time.monotonic

#: Seconds one category may spend being counted. A storage screen that hangs for a minute on a
#: worktree full of `node_modules` is worse than one that says "not measured" for that row.
CATEGORY_BUDGET_SECONDS = 5.0

#: Seconds the WHOLE report may spend; each count stops at the earlier of its own budget and this.
#: A budget per count alone let one report take (named categories + worktrees + 2) x 5 s — a minute
#: or more with a few worktrees full of `node_modules` — and the Settings screen asked for two.
REPORT_BUDGET_SECONDS = 20.0

#: How long a finished report answers a repeat request for the same home, workspace and worktree
#: setting. The Storage and Diagnostics cards mount together and each asks for the report; they get
#: one walk of the disk between them, not two at once. Short, and dropped by every action that
#: changes what it counts, so a screen never shows a size a button just changed.
SHARED_REPORT_SECONDS = 5.0

#: Where the worktree total starts to warn, in GiB, and how many idle days make a conversation worth
#: offering to archive. Both are PROPOSALS read off one measured machine (`study/auditoria-2026-11/
#: areas/a12-worktrees-medicao.md`: 89 checkouts, median 0.77 GiB, all idle 14-21 days), not laws:
#: the owner changes them in Settings. 10 GiB would have warned at the 14th checkout.
WORKTREE_LIMIT_GB = 50.0
WORKTREE_IDLE_DAYS = 14.0

#: A bytes figure for a worktree stays good this long when the report asks again. Counting 89
#: checkouts of ~45 000 files takes minutes and a report has 20 s, so each look keeps what it
#: counted and the next one continues; a size is a few percent stale at worst, and says nothing it
#: did not count. Used only by `measure_shared` (the screen), never by a plain `measure`.
SIZE_CACHE_SECONDS = 900.0

#: The traces `rotate_logs` may move aside. Only files whose writers ALREADY rotate them at a cap
#: (`chimera/core/steplog.py`, renamed to `.1` keeping one generation): rotating them earlier is a
#: choice their writer makes on its own, so nothing that reads them can be surprised by it. Left out
#: on purpose — `usage.jsonl` (the daily spend cap reads it: rotating it would reset today's spend),
#: `audit.jsonl` and `approvals/history.jsonl` (the record of what was allowed), `trajectories.jsonl`
#: (the evidence the learning loop trains on), `voice/requests.jsonl` (a corpus being labelled).
ROTATABLE_LOGS: tuple[str, ...] = ("traces.jsonl", "scheduler/cron_traces.jsonl")

#: The one category the owner may clear without affecting conversation state or credentials.
#: Keep this a literal child of the fixed home, never a caller-supplied path or a glob.
CACHE_DIRECTORY = "cache"

#: Every category, in the order a screen shows them. ``other`` is what the home holds outside the
#: named ones; the worktrees and the browsers live outside the home.
CATEGORY_KEYS: tuple[str, ...] = (
    "sessions",
    "history",
    "memory",
    "scheduler",
    "approvals",
    "bundles",
    "designs",
    "voice",
    "logs",
    "cache",
    "other",
    "worktrees",
    "browsers",
)

#: Where each home category lives, relative to the home. Globs, so a SQLite store's `-wal` and
#: `-shm` files are counted with it — they are part of it, and on a busy store the larger part.
_HOME_CATEGORIES: dict[str, tuple[str, ...]] = {
    "sessions": ("sessions", "code_sessions"),
    "history": ("history.db*",),
    "memory": ("memory.json", "memory.db*", "memory_graph.json"),
    "approvals": ("approvals",),
    "bundles": ("skills",),
    # Chimera Design documents and their version snapshots, with each document's lock file.
    "designs": ("designs",),
    "voice": ("voice",),
    "logs": tuple(f"{name}*" for name in ROTATABLE_LOGS),
    "cache": ("cache",),
    # Last among the folders, so the traces under `scheduler/` above are counted as logs only.
    "scheduler": ("scheduler",),
}


@dataclass
class Size:
    """Bytes and files under a path; both None when the count is not a measurement."""

    bytes: int | None
    files: int | None
    note: str = ""


def _is_link(entry: os.DirEntry[str]) -> bool:
    """Symlinks AND Windows junctions, neither of which is followed: a junction inside a worktree
    pointing at a whole drive would otherwise be counted as this install's disk use."""
    if entry.is_symlink():
        return True
    junction = getattr(entry, "is_junction", None)
    return bool(junction()) if callable(junction) else False


def tree_size(
    path: Path,
    *,
    exclude: Iterable[Path] = (),
    deadline: float | None = None,
) -> Size:
    """Count the bytes and files under ``path``, without following links.

    A path that does not exist is a measurement: zero. A folder that cannot be read, or a count that
    reaches ``deadline`` (a :data:`clock` value), is not, and comes back as None with a note saying
    which. ``exclude`` names files counted by another category.
    """
    skip = {os.path.normcase(str(p)) for p in exclude}
    path = Path(path)
    try:
        if not path.exists():
            return Size(0, 0)
        if path.is_file():
            return Size(path.stat().st_size, 1)
    except OSError as exc:
        return Size(None, None, f"{path.name}: {type(exc).__name__}")
    total, files = 0, 0
    stack = [str(path)]
    while stack:
        # Reached, not passed: a deadline is a multiple of the clock's step plus a whole budget, so on
        # a coarse clock "now == deadline" is a reading that lasts a full step (15.6 ms on Windows) —
        # and with ``>`` every walk started in that step ran to the end and reported a size.
        if deadline is not None and clock() >= deadline:
            return Size(None, None, "stopped counting at the time limit")
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if _is_link(entry) or os.path.normcase(entry.path) in skip:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                        files += 1
        except OSError as exc:
            return Size(None, None, f"{Path(current).name}: {type(exc).__name__}")
    return Size(total, files)


def _sum(sizes: Iterable[Size]) -> Size:
    """Add sizes; one unmeasured part makes the whole unmeasured, and its note travels with it."""
    total, files = 0, 0
    for size in sizes:
        if size.bytes is None or size.files is None:
            return Size(None, None, size.note)
        total += size.bytes
        files += size.files
    return Size(total, files)


def _matches(home: Path, patterns: Iterable[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            try:
                found.extend(sorted(home.glob(pattern)))
            except OSError:
                continue
        else:
            found.append(home / pattern)
    return found


def playwright_dir() -> tuple[Path | None, str]:
    """Where Playwright keeps its browsers on this machine, or None and why.

    The agent's browser launches a fresh profile per session and deletes it on close, so the only
    browser bytes this install leaves on disk are the browser builds themselves.
    """
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured == "0":
        return None, "browsers are kept inside the playwright package"
    if configured:
        return Path(configured).expanduser(), ""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if not local:
            return None, "LOCALAPPDATA is not set"
        return Path(local) / "ms-playwright", ""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright", ""
    cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(cache) if cache else Path.home() / ".cache") / "ms-playwright", ""


@dataclass
class Category:
    key: str
    size: Size
    paths: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bytes": self.size.bytes,
            "files": self.size.files,
            "paths": list(self.paths),
            "note": self.size.note,
        }


def _disk(path: Path) -> tuple[int | None, dict[str, Any]]:
    """Free and total space of the drive holding ``path``, and that drive's device number (so two
    places on one drive are reported once). None for what could not be read."""
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        device = os.stat(probe).st_dev
        usage = shutil.disk_usage(probe)
    except OSError:
        return None, {"path": str(path), "total": None, "free": None}
    return device, {"path": str(path), "total": int(usage.total), "free": int(usage.free)}


def _budget(report_deadline: float) -> float:
    """One count's deadline: its own budget, cut short by what is left of the report's."""
    return min(clock() + CATEGORY_BUDGET_SECONDS, report_deadline)


def _home_categories(
    home: Path, worktree_parent: Path, report_deadline: float
) -> tuple[list[Category], Size]:
    """The named categories under the home, and the home's total.

    Each file is counted once: a category walks its folders without the paths an earlier one
    already claimed (the scheduler's traces are logs, not schedules). The worktree folder is left
    out of the total for the same reason, in case the owner put it inside the home.
    """
    claimed: list[Path] = []
    categories: list[Category] = []
    for key, patterns in _HOME_CATEGORIES.items():
        paths = _matches(home, patterns)
        size = _sum(tree_size(p, exclude=claimed, deadline=_budget(report_deadline)) for p in paths)
        categories.append(Category(key, size, [str(p) for p in paths if p.exists()]))
        claimed.extend(paths)
    return categories, tree_size(
        home, exclude=[worktree_parent], deadline=_budget(report_deadline)
    )


@dataclass(frozen=True)
class WorktreeOwner:
    """One conversation whose workspace is a worktree folder, as the Storage report needs it.

    Built by the caller from the conversation list's own records (`chimera/api/storage_api.py`), so
    the rules for "archived", "due" and "kept" stay where they are decided
    (`worktree_expiry.removal_view`): this module copies none of them.
    """

    session_id: str
    title: str
    archived: bool
    #: The worktree folder as the conversation stores it (case kept), so a project other than the
    #: server's own is listed too.
    folder: str = ""
    #: Days since the conversation was last written to or brought back; None once archived.
    idle_days: float | None = None
    #: When the automatic clean-up may remove its worktree (epoch seconds), None while that is off or
    #: the conversation is not archived.
    due_at: float | None = None
    #: The refusal the last clean-up pass recorded ("dirty", "unpushed", ...), "" when none.
    kept: str = ""


def owner_key(path: Path | str) -> str:
    """The form a folder is looked up by: resolved and case-folded where the platform folds case."""
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except OSError:
        return os.path.normcase(str(path))


def _retention(owners: Sequence[WorktreeOwner]) -> dict[str, Any]:
    """Why a worktree still exists, as a fixed word a screen translates.

    ``conversation`` - a conversation that is not archived uses it, and nothing here lets it go.
    ``archived`` - every conversation using it is archived: ``retention_due_at`` is when the clean-up
    may remove it, and ``retention_kept`` is why the last pass kept it (dirty tree, a commit not
    pushed...). ``no_conversation`` - no conversation points at it. The refusals themselves stay the
    clean-up's; this only reads what it wrote down.
    """
    if not owners:
        return {"retention": "no_conversation", "retention_due_at": None, "retention_kept": ""}
    live = [o for o in owners if not o.archived]
    if live:
        first = min(live, key=lambda o: o.idle_days if o.idle_days is not None else 0.0)
        return {
            "retention": "conversation", "retention_due_at": None, "retention_kept": "",
            "session_id": first.session_id, "title": first.title,
        }
    due = [o.due_at for o in owners if o.due_at is not None]
    kept = next((o.kept for o in owners if o.kept), "")
    return {
        "retention": "archived",
        "retention_due_at": max(due) if due else None,
        "retention_kept": kept,
        "session_id": owners[0].session_id, "title": owners[0].title,
    }


def _worktree_size(
    path: Path, deadline: float, cache: dict[str, tuple[float, Size]] | None
) -> Size:
    """The size of one worktree: from ``cache`` when it holds a recent count, else counted now. A count
    that ran out of time is not cached - "not measured" is asked again next time."""
    key = owner_key(path)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None and clock() - hit[0] < SIZE_CACHE_SECONDS:
            return hit[1]
    size = tree_size(path, deadline=deadline)
    if cache is not None and size.bytes is not None:
        cache[key] = (clock(), size)
    return size


def _archive_candidates(
    rows: list[dict[str, Any]],
    owners: Mapping[str, Sequence[WorktreeOwner]],
    idle_days: float,
) -> list[dict[str, Any]]:
    """The idle conversations that could be archived: not archived, idle at least ``idle_days``, and
    holding a worktree this report lists that no run of this process is using. The biggest first.
    A list, not an action - nothing is archived until the owner presses the button, and archiving
    touches nothing on disk."""
    found: list[dict[str, Any]] = []
    for row in rows:
        if row["state"] == "live":
            continue
        for owner in owners.get(owner_key(row["path"]), ()):
            if owner.archived or owner.idle_days is None or owner.idle_days < idle_days:
                continue
            found.append({
                "session_id": owner.session_id, "title": owner.title,
                "idle_days": round(owner.idle_days, 1), "path": row["path"], "bytes": row["bytes"],
            })
    found.sort(key=lambda c: (c["bytes"] is None, -(c["bytes"] or 0), -c["idle_days"]))
    return found


def _worktree_folders(
    workspace: Path | None,
    owners: Mapping[str, Sequence[WorktreeOwner]],
    find: Callable[[Path | None], list[Path]],
) -> list[Path]:
    """Every conversation worktree folder worth listing: the ones the folders of this project hold,
    and the ones a conversation points at wherever they are (another project's durable folder).
    Only a folder named like a conversation's worktree and present on disk; each listed once."""
    from chimera.core.worktree import WORKTREE_PREFIX

    folders = {owner_key(p): p for p in find(workspace)}
    for group in owners.values():
        for owner in group:
            path = Path(owner.folder) if owner.folder else None
            if path is None or not path.name.startswith(WORKTREE_PREFIX):
                continue
            try:
                if path.is_dir():
                    folders.setdefault(owner_key(path), path)
            except OSError:
                continue
    return sorted(folders.values())


def measure(
    home: Path,
    workspace: Path | None = None,
    *,
    owners: Mapping[str, Sequence[WorktreeOwner]] | None = None,
    limit_gb: float = WORKTREE_LIMIT_GB,
    idle_days: float = WORKTREE_IDLE_DAYS,
    size_cache: dict[str, tuple[float, Size]] | None = None,
) -> dict[str, Any]:
    """The whole report: categories, the worktrees one by one, and the drives they sit on.

    ``workspace`` is the project a run would make its worktree from. ``worktree_dir`` is where THAT
    worktree goes, so the screen's "the next worktree goes to" is what `GitWorktree.create` will do:
    a configured folder inside the project is passed over for temp there, and must be here too. The
    worktrees listed are the ones of THAT project too (its durable folder beside it), not only temp's.

    ``owners`` maps a worktree folder (:func:`owner_key`) to the conversations that use it; without it
    every worktree reads as having none. ``limit_gb`` and ``idle_days`` are the owner's two settings.
    """
    from chimera.core.worktree import classify_worktree_dir, find_worktree_dirs, worktree_parent

    report_deadline = clock() + REPORT_BUDGET_SECONDS
    home = Path(home).resolve()
    parent = worktree_parent(workspace)
    named, home_total = _home_categories(home, parent, report_deadline)
    by_key = {c.key: c for c in named}

    measured = [c.size for c in named]
    if home_total.bytes is None or any(s.bytes is None for s in measured):
        other = Size(None, None, home_total.note or "a category above was not measured")
    else:
        rest = home_total.bytes - sum(s.bytes or 0 for s in measured)
        rest_files = (home_total.files or 0) - sum(s.files or 0 for s in measured)
        other = Size(max(rest, 0), max(rest_files, 0))
    by_key["other"] = Category("other", other, [str(home)])

    worktrees: list[dict[str, Any]] = []
    sizes: list[Size] = []
    by_folder = owners or {}
    now = time.time()
    for path in _worktree_folders(workspace, by_folder, find_worktree_dirs):
        state = classify_worktree_dir(path)
        size = _worktree_size(path, _budget(report_deadline), size_cache)
        sizes.append(size)
        try:
            age: float | None = round(max(0.0, now - path.stat().st_mtime) / 86400.0, 1)
        except OSError:
            age = None
        worktrees.append({
            "path": str(path), "bytes": size.bytes, "state": state.state, "reason": state.reason,
            "age_days": age, "session_id": "", "title": "",
            **_retention(by_folder.get(owner_key(path), ())),
        })
    by_key["worktrees"] = Category("worktrees", _sum(sizes), [str(parent)])
    counted = sum(s.bytes for s in sizes if s.bytes is not None)
    unmeasured = sum(1 for s in sizes if s.bytes is None)
    limit_bytes = int(max(limit_gb, 0.0) * 1024**3)
    # Over the limit is certain from what WAS counted (a part can only add); under it is certain only
    # when everything was counted. Otherwise the report does not know, and says so rather than "no".
    over: bool | None = True if counted > limit_bytes else (False if unmeasured == 0 else None)

    browsers, why = playwright_dir()
    by_key["browsers"] = Category(
        "browsers",
        tree_size(browsers, deadline=_budget(report_deadline)) if browsers else Size(None, None, why),
        [str(browsers)] if browsers else [],
    )

    drives: list[dict[str, Any]] = []
    seen: set[int] = set()
    for place in (home, parent):
        device, disk = _disk(place)
        if device is None or device not in seen:
            drives.append(disk)
        if device is not None:
            seen.add(device)

    return {
        "home": str(home),
        "worktree_dir": str(parent),
        "categories": [by_key[k].as_dict() for k in CATEGORY_KEYS],
        "worktrees": worktrees,
        "worktrees_counted_bytes": counted,
        "worktrees_unmeasured": unmeasured,
        "worktrees_limit_gb": limit_gb,
        "worktrees_over_limit": over,
        "archive_idle_days": idle_days,
        "archive_candidates": _archive_candidates(worktrees, by_folder, idle_days),
        "disks": drives,
        "rotatable_logs": list(ROTATABLE_LOGS),
    }


_shared_lock = threading.Lock()
_shared: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_flights: dict[tuple[str, str, str], threading.Lock] = {}
#: What each screen look counted of the worktrees, so the next look continues instead of starting over.
_worktree_sizes: dict[str, tuple[float, Size]] = {}


def measure_shared(
    home: Path,
    workspace: Path | None = None,
    *,
    owners: Callable[[], Mapping[str, Sequence[WorktreeOwner]]] | None = None,
    limit_gb: float = WORKTREE_LIMIT_GB,
    idle_days: float = WORKTREE_IDLE_DAYS,
) -> dict[str, Any]:
    """:func:`measure`, with one walk serving every caller that asks at about the same time.

    A request that arrives while a measurement of the same thing is running waits for it instead of
    starting a second walk of the same disk; one that arrives within ``SHARED_REPORT_SECONDS`` after
    it gets its result. Keyed by the worktree setting and the two limits too, so saving a new one is
    seen at once. Callers get a copy — nothing they do to it reaches the next caller. ``owners`` is a
    function, called only when a walk is actually made.
    """
    from chimera.config import get_settings

    key = (
        os.path.normcase(str(Path(home).resolve())),
        str(workspace or ""),
        f"{(get_settings().worktree_dir or '').strip()}|{limit_gb}|{idle_days}",
    )
    with _shared_lock:
        flight = _flights.setdefault(key, threading.Lock())
    with flight:
        with _shared_lock:
            cached = _shared.get(key)
        if cached is not None and clock() - cached[0] < SHARED_REPORT_SECONDS:
            return copy.deepcopy(cached[1])
        report = measure(
            home, workspace, owners=owners() if owners is not None else None,
            limit_gb=limit_gb, idle_days=idle_days, size_cache=_worktree_sizes,
        )
        with _shared_lock:
            _shared[key] = (clock(), report)
        return copy.deepcopy(report)


def forget_shared_reports() -> None:
    """Drop every shared report: called by the actions that change what one counts."""
    with _shared_lock:
        _shared.clear()


def _is_link_path(path: Path) -> bool:
    """A symlink OR a Windows junction. ``Path.is_symlink`` is False for a junction, and
    ``shutil.rmtree`` on one recurses into its target on Python 3.11 — a ``cache`` junction pointing
    at the owner's Documents would be emptied by a button labelled "clear cache"."""
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction(path)) if callable(isjunction) else False


def clear_cache(home: Path) -> dict[str, int]:
    """Remove only ``<home>/cache`` without following a link out of the home.

    The category in the Storage card is the contract: sessions, memory, settings and secrets are
    different directories and are never candidates. The folder name is :data:`CACHE_DIRECTORY`, a
    literal — no caller supplies a path or a pattern. A link at ``cache`` (symlink or junction) is
    unlinked rather than followed. If removal fails, report it: the caller restarts the backend
    only after a clear that worked, so an unsuccessful clear never turns into a restart loop.
    """
    path = Path(home) / CACHE_DIRECTORY
    try:
        if _is_link_path(path):
            try:
                path.unlink()
            except OSError:
                # A directory junction refuses `unlink` on some Windows builds; `rmdir` removes the
                # junction itself and never what it points at.
                os.rmdir(path)
            forget_shared_reports()
            return {"removed": 1, "bytes_freed": 0, "failed": 0}
        if not path.is_dir():
            return {"removed": 0, "bytes_freed": 0, "failed": 0}
        measured = tree_size(path)
        bytes_freed = measured.bytes or 0
        shutil.rmtree(path)
        forget_shared_reports()
        return {"removed": 1, "bytes_freed": bytes_freed, "failed": 0}
    except OSError as exc:
        _log.info("could not clear cache %s: %s", path, exc)
        return {"removed": 0, "bytes_freed": 0, "failed": 1}


def rotate_logs(home: Path) -> dict[str, int]:
    """Move each rotatable trace to ``.1``, dropping the previous ``.1``. Returns what happened.

    The same rename its writer performs at the size cap, done now: one previous generation is kept,
    so a trace being read to investigate something is still there after one press. ``bytes_freed``
    counts only the generations dropped — the first rotation of a file frees nothing, and says so.
    A file a writer holds open (Windows refuses the rename) is counted as failed and left alone.
    """
    result = {"rotated": 0, "bytes_freed": 0, "failed": 0}
    for name in ROTATABLE_LOGS:
        path = Path(home) / name
        try:
            if not path.is_file() or path.stat().st_size == 0:
                continue
            previous = path.with_suffix(path.suffix + ".1")
            dropped = previous.stat().st_size if previous.is_file() else 0
            path.replace(previous)
        except OSError as exc:
            _log.info("could not rotate %s: %s", name, exc)
            result["failed"] += 1
            continue
        result["rotated"] += 1
        result["bytes_freed"] += dropped
    return result


def format_bytes(value: int | None) -> str:
    """A size a person reads at a glance; ``not measured`` for None, never ``0 B``."""
    if value is None:
        return "not measured"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover - the loop always returns


def summary_rows(report: dict[str, Any]) -> list[tuple[str, str]]:
    """The report as label/value lines — what `chimera doctor` prints and the Copy button copies.

    One function for both, so the terminal and the app cannot describe the same disk differently.
    """
    rows: list[tuple[str, str]] = []
    for category in report["categories"]:
        value = format_bytes(category["bytes"])
        if category["bytes"] is None and category["note"]:
            value += f" ({category['note']})"
        rows.append((str(category["key"]), value))
    orphans = [w for w in report["worktrees"] if w["state"] == "orphan"]
    if report["worktrees"]:
        rows.append(("worktree folders", f"{len(report['worktrees'])} ({len(orphans)} orphaned)"))
    rows.append(("worktree location", str(report["worktree_dir"])))
    for disk in report["disks"]:
        rows.append((f"free on {disk['path']}", format_bytes(disk["free"])))
    return rows
