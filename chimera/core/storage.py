"""What this install keeps on disk, by kind — and the two things that may safely be let go.

Study 29, P5.3. Nothing measured disk until now: `chimera/core/resources.py` reads CPU, memory and
VRAM, while the isolated worktrees — each a full checkout of the repository — went to the system
temp folder, on the same drive that has already filled once on this machine.

The rule is the one `resources.py` states: **a measurement that could not be taken is reported as
absent, never as zero.** A category reads ``bytes: null`` when its folder could not be read, when
counting it ran past its time budget, or when its location is unknown here; ``0`` means it was
counted and nothing was there. A partial count is not a measurement either: a lower bound shown as
a size is believed as one.

What may be removed from here is deliberately short: orphaned worktrees
(:func:`chimera.core.worktree.prune_worktree_dirs`) and one rotation of the diagnostic traces
(:func:`rotate_logs`). Sessions, memory, history, approvals and schedules are measured and never
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
from collections.abc import Callable, Iterable
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

#: The traces `rotate_logs` may move aside. Only files whose writers ALREADY rotate them at a cap
#: (`chimera/core/steplog.py`, renamed to `.1` keeping one generation): rotating them earlier is a
#: choice their writer makes on its own, so nothing that reads them can be surprised by it. Left out
#: on purpose — `usage.jsonl` (the daily spend cap reads it: rotating it would reset today's spend),
#: `audit.jsonl` and `approvals/history.jsonl` (the record of what was allowed), `trajectories.jsonl`
#: (the evidence the learning loop trains on), `voice/requests.jsonl` (a corpus being labelled).
ROTATABLE_LOGS: tuple[str, ...] = ("traces.jsonl", "scheduler/cron_traces.jsonl")

#: Every category, in the order a screen shows them. ``other`` is what the home holds outside the
#: named ones; the worktrees and the browsers live outside the home.
CATEGORY_KEYS: tuple[str, ...] = (
    "sessions",
    "history",
    "memory",
    "scheduler",
    "approvals",
    "bundles",
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


def measure(home: Path, workspace: Path | None = None) -> dict[str, Any]:
    """The whole report: categories, the worktrees one by one, and the drives they sit on.

    ``workspace`` is the project a run would make its worktree from. ``worktree_dir`` is where THAT
    worktree goes, so the screen's "the next worktree goes to" is what `GitWorktree.create` will do:
    a configured folder inside the project is passed over for temp there, and must be here too.
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
    for path in find_worktree_dirs():
        state = classify_worktree_dir(path)
        size = tree_size(path, deadline=_budget(report_deadline))
        sizes.append(size)
        worktrees.append(
            {"path": str(path), "bytes": size.bytes, "state": state.state, "reason": state.reason}
        )
    by_key["worktrees"] = Category("worktrees", _sum(sizes), [str(parent)])

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
        "disks": drives,
        "rotatable_logs": list(ROTATABLE_LOGS),
    }


_shared_lock = threading.Lock()
_shared: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_flights: dict[tuple[str, str, str], threading.Lock] = {}


def measure_shared(home: Path, workspace: Path | None = None) -> dict[str, Any]:
    """:func:`measure`, with one walk serving every caller that asks at about the same time.

    A request that arrives while a measurement of the same thing is running waits for it instead of
    starting a second walk of the same disk; one that arrives within ``SHARED_REPORT_SECONDS`` after
    it gets its result. Keyed by the worktree setting too, so saving a new folder is seen at once.
    Callers get a copy — nothing they do to it reaches the next caller.
    """
    from chimera.config import get_settings

    key = (
        os.path.normcase(str(Path(home).resolve())),
        str(workspace or ""),
        (get_settings().worktree_dir or "").strip(),
    )
    with _shared_lock:
        flight = _flights.setdefault(key, threading.Lock())
    with flight:
        with _shared_lock:
            cached = _shared.get(key)
        if cached is not None and clock() - cached[0] < SHARED_REPORT_SECONDS:
            return copy.deepcopy(cached[1])
        report = measure(home, workspace)
        with _shared_lock:
            _shared[key] = (clock(), report)
        return copy.deepcopy(report)


def forget_shared_reports() -> None:
    """Drop every shared report: called by the actions that change what one counts."""
    with _shared_lock:
        _shared.clear()


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
