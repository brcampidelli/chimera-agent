"""Orchestration runs, on disk, so closing the tab does not throw away what was paid for.

A fan-out costs a top-model decompose, N workers and a synthesis. Until now every frame of that
existed only in an SSE stream: close the app, reload the page, or lose the connection, and the
answer was gone while the bill stayed. The cost was recorded and the product was not.

Append-only JSONL, one file per run, with the `seq` the endpoint already stamps under a lock at its
single writer. That number is what makes replay safe: a client that has seen up to `seq` asks for
what came after, and the reducer it already owns ignores anything it has — so replay-then-live and
live-only converge on the same state, which is the property the whole design turns on.

Deliberately not `runs.db`/`RunCheckpointer`: that is for a run PAUSED awaiting a human verdict, a
different thing with a different lifecycle. This is a transcript.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("orchestration.runlog")

#: One lock per process, not per file. Two runs write different files, but the OS-level append is
#: what makes a partial line impossible and a lock here is what makes the reader's job simple.
_write_lock = threading.Lock()

#: How many runs the listing keeps. A transcript is small; a home directory that grows without a
#: ceiling is a support ticket about disk space six months from now.
MAX_RUNS = 50

#: Bytes after which a run's log stops growing. A pathological run — a worker looping on a huge
#: observation — must not fill the disk to preserve a transcript nobody will read to the end.
MAX_BYTES = 4 * 1024 * 1024


#: Where a transcript lives, by the surface that produced it. Separate directories so a listing of
#: one never has to filter out the other, and so pruning one cannot reach the other's runs.
AREAS = ("orchestration", "code")


def run_dir(home: Path, run_id: str, *, area: str = "orchestration") -> Path:
    """Where one run's transcript lives. The id is hex from `uuid4`, so it needs no sanitising —
    but it is checked anyway, because a path built from a request parameter is a path built from a
    request parameter.

    ``area`` is checked against a fixed list rather than sanitised. It is not user input today, and
    the day it becomes user input a whitelist is the difference between a directory name and a path
    traversal — that is not a bet worth taking twice.
    """
    if area not in AREAS:
        raise ValueError(f"unknown transcript area {area!r}")
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")[:64]
    if not safe:
        raise ValueError("empty run id")
    return Path(home) / area / safe


@dataclass(frozen=True)
class RunSummary:
    """A run in the list, as much as can be known without reading the whole transcript."""

    run_id: str
    task: str
    kind: str  # "hierarchy" | "crew"
    started: float
    frames: int
    done: bool


def append(
    home: Path, run_id: str, event: str, payload: dict[str, Any], *, area: str = "orchestration"
) -> None:
    """Record one frame. Best-effort: a transcript that cannot be written must not fail the run.

    The run is the product and it is already being paid for; losing the record of it is bad, and
    losing the run to preserve the record would be worse.
    """
    try:
        directory = run_dir(home, run_id, area=area)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "frames.jsonl"
        line = json.dumps({"event": event, **payload}, ensure_ascii=False, default=str)
        with _write_lock:
            if path.exists() and path.stat().st_size > MAX_BYTES:
                return
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        _log.debug("orchestration frame not persisted: %s", exc)


def exists(home: Path, run_id: str, *, area: str = "orchestration") -> bool:
    """Whether this run was ever recorded.

    `frames()` cannot answer it: an unknown id and a run that has not produced anything past
    ``since`` both come back empty, and a caller cannot tell "no such run" from "nothing new yet"
    — which are opposite instructions for a client deciding whether to keep polling.
    """
    try:
        return (run_dir(home, run_id, area=area) / "frames.jsonl").exists()
    except ValueError:
        return False


def frames(
    home: Path, run_id: str, *, since: int = 0, area: str = "orchestration"
) -> list[dict[str, Any]]:
    """Every frame after ``since``, oldest first.

    A malformed line is skipped rather than fatal: the file is appended to from a live run, and a
    reader that refused the whole transcript over one truncated tail line would lose the ninety-nine
    frames it could have replayed.
    """
    try:
        path = run_dir(home, run_id, area=area) / "frames.jsonl"
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, ValueError) as exc:
        _log.debug("orchestration transcript unreadable: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, dict) and int(frame.get("seq") or 0) > since:
            out.append(frame)
    return out


def _summary(directory: Path) -> RunSummary | None:
    path = directory / "frames.jsonl"
    if not path.exists():
        return None
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        first = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    # `done` from the LAST frame that says so, not from the file merely ending: a run killed with
    # the process leaves a transcript that stops, and reporting that as finished would turn a
    # crash into a completed run in the one list built to find them again.
    done = any('"done"' in line or '"crew_done"' in line for line in lines[-3:])
    # Both engines open with a plain `run` frame, so the first line cannot say which one this was.
    # The crew prefixes every worker frame; the hierarchy does not. Scanned rather than recorded
    # separately, so there is one source of truth about it and it is the transcript itself.
    return RunSummary(
        run_id=directory.name,
        task=str(first.get("task") or ""),
        kind="crew" if any('"crew_' in line for line in lines) else "hierarchy",
        started=path.stat().st_mtime,
        frames=len(lines),
        done=done,
    )


def recent(home: Path, *, limit: int = 20) -> list[RunSummary]:
    """The most recently written runs, newest first."""
    root = Path(home) / "orchestration"
    if not root.is_dir():
        return []
    found = [s for d in root.iterdir() if d.is_dir() and (s := _summary(d)) is not None]
    found.sort(key=lambda s: s.started, reverse=True)
    return found[:limit]


def prune(home: Path, *, keep: int = MAX_RUNS) -> int:
    """Drop the oldest transcripts beyond ``keep``. Returns how many were removed."""
    import shutil

    root = Path(home) / "orchestration"
    if not root.is_dir():
        return 0
    dirs = sorted(
        (d for d in root.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for directory in dirs[keep:]:
        try:
            shutil.rmtree(directory)
            removed += 1
        except OSError as exc:  # noqa: PERF203 -- one failure must not stop the rest
            _log.debug("could not prune %s: %s", directory, exc)
    return removed
