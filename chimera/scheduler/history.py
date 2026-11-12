"""What happened at every scheduled time, including the times that did not run.

``cron_results.jsonl`` holds the ANSWERS: a line exists only when a dispatch produced one. That is
the right file for "what did my 7 o'clock report say" and the wrong one for "why did nothing happen
yesterday", because every reason a time can pass without a run — the app was closed, the job was
switched off, the day's money was spent — leaves no answer and therefore no line. A schedule that
skips in silence reads exactly like a schedule with nothing due, which is the distinction
:meth:`~chimera.scheduler.engine.Scheduler.overdue` was written to expose and could only ever answer
for the time being late, not for the time being given up.

So this is a second file, one line per scheduled TIME (or per notable event of a run), each with a
reason in a closed vocabulary. It is a record, never an input: nothing reads it to decide whether to
run, so a damaged or missing file costs the screen its history and costs the schedule nothing.

**Read from the end**, for the reason ``results.py`` gives: the file is append-only and grows for the
life of the install.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from chimera.scheduler.results import _tail_lines
from chimera.telemetry import get_logger

_log = get_logger("scheduler.history")

#: Beside ``jobs.json`` and ``cron_results.jsonl``: one folder is the whole scheduler's state.
HISTORY_FILENAME = "cron_history.jsonl"

TAIL_BYTES = 512 * 1024

Outcome = Literal["ran", "skipped", "note"]
"""``ran``: a dispatch started (its ``status`` says how it ended). ``skipped``: a scheduled time
that did not run, and ``reason`` says why. ``note``: something worth keeping about a run that is not
a time at all — a worktree left behind, a permission mode narrowed."""

#: The closed set of reasons, so the screen can put each in words in every language and a typo in
#: the engine cannot invent a sentence nobody translated. ``tests/test_scheduled_catch_up_history.py``
#: holds the desktop's table equal to this one.
REASONS: tuple[str, ...] = (
    "on_time",  # ran at its time
    "catch_up",  # ran once, late, because the time was missed
    "catch_up_merged",  # a missed time that the one catch-up run stood in for
    "catch_up_skip",  # missed, and the job's policy is skip
    "missed_too_old",  # missed by more than seven days
    "disabled",  # the job was switched off at that time
    "already_running",  # the previous dispatch of this job had not ended
    "budget_exceeded",  # refused because the day's money was spent
    "timeout",  # ran, was abandoned at the job timeout
    "error",  # ran and raised
    "rejected",  # ran, and its own verify command threw the work away
    "cancelled",  # stopped by the operator
    "worktree_kept",  # note: the run left work in a worktree, path in `worktree`
    "permission_narrowed",  # note: the mode was wider than Settings allow now, and ran narrower
)


@dataclass(frozen=True)
class HistoryRow:
    """One line of the history."""

    at: float
    """When this was decided or finished, on the scheduler's clock."""
    job_id: str
    name: str
    outcome: Outcome
    reason: str
    status: str = ""
    """How a dispatch ended (``ok``, ``error``, ``timeout``, ``budget``, ``rejected``,
    ``cancelled``); empty on a time that did not run."""
    scheduled_for: float | None = None
    """The scheduled time this row is about; None for a note."""
    detail: str = ""
    worktree: str = ""
    """Where the run's worktree was left, when it was kept."""


#: The file is rotated past this size, keeping the newest :data:`KEEP_PER_JOB` lines of each job. Per
#: job and not a plain tail: a minute-by-minute job writes thousands of lines a day, and a tail would
#: push a weekly job's only line out of the file within hours.
MAX_BYTES = 4 * 1024 * 1024
KEEP_PER_JOB = 300

#: A rotation brings the file down to this share of :data:`MAX_BYTES`.
ROTATE_TO = 0.75

#: The most of the file a read goes back through to find one job's lines.
MAX_SCAN_BYTES = 8 * 1024 * 1024


def _row_of(line: str) -> HistoryRow | None:
    """A row from one line, or None for anything that is not one. A line that is valid JSON with a
    field of the wrong shape (an ``at`` that is not a number) is as unreadable as a torn write, and
    must not raise into the tick or the route that asked."""
    try:
        d = json.loads(line)
        if not isinstance(d, dict):
            return None
        outcome = str(d.get("outcome") or "")
        if outcome not in ("ran", "skipped", "note"):
            return None
        scheduled = d.get("scheduled_for")
        return HistoryRow(
            at=float(d.get("at") or 0.0),
            job_id=str(d.get("job_id") or ""),
            name=str(d.get("name") or ""),
            outcome=cast("Outcome", outcome),
            reason=str(d.get("reason") or ""),
            status=str(d.get("status") or ""),
            scheduled_for=float(scheduled) if isinstance(scheduled, (int, float)) else None,
            detail=str(d.get("detail") or ""),
            worktree=str(d.get("worktree") or ""),
        )
    except (ValueError, TypeError, OverflowError):
        return None


class CronHistory:
    """Append and read the history file. Never raises on I/O: a record must not cost a tick."""

    def __init__(
        self, path: Path, *, max_bytes: int = MAX_BYTES, keep_per_job: int = KEEP_PER_JOB
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.keep_per_job = keep_per_job

    def append(self, row: HistoryRow) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
            if self.path.stat().st_size > self.max_bytes:
                self._rotate()
        except OSError as exc:
            _log.warning("cron history not written (%s): %s", row.job_id, exc)

    def _rotate(self) -> None:
        """Keep the newest ``keep_per_job`` lines of each job, in their original order.

        Rewritten through a unique temp file and an atomic replace, like the job store: a reader
        never sees half a file. A line another process appends during the rewrite can be lost; this
        is a record, and that is the price of not taking a lock for it.
        """
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        # Down to a share of the limit, not to it: rewriting the file every time one more line
        # crosses the limit would re-read and re-write megabytes on every append.
        target = int(self.max_bytes * ROTATE_TO)
        cap = self.keep_per_job
        while True:
            seen: dict[str, int] = {}
            keep: list[str] = []
            for line in reversed(lines):
                row = _row_of(line)
                if row is None:
                    continue
                seen[row.job_id] = seen.get(row.job_id, 0) + 1
                if seen[row.job_id] <= cap:
                    keep.append(line)
            if cap <= 1 or sum(len(k.encode("utf-8")) + 1 for k in keep) <= target:
                break
            cap //= 2
        keep.reverse()
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def load(self, *, job_id: str | None = None, limit: int = 100) -> list[HistoryRow]:
        """The newest rows first, going back through the file until ``limit`` rows of the job are
        found or :data:`MAX_SCAN_BYTES` have been read. A malformed line is skipped."""
        if not self.path.exists():
            return []
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        window = TAIL_BYTES
        while True:
            try:
                lines = _tail_lines(self.path, window)
            except OSError:
                return []
            out: list[HistoryRow] = []
            for line in reversed(lines):
                if not line.strip():
                    continue
                row = _row_of(line)
                if row is None or (job_id is not None and row.job_id != job_id):
                    continue
                out.append(row)
                if len(out) >= limit:
                    return out
            if window >= size or window >= MAX_SCAN_BYTES:
                return out
            window = min(window * 4, MAX_SCAN_BYTES)


def history_path(home: Path) -> Path:
    """Where the history of the install rooted at ``home`` lives."""
    return Path(home) / "scheduler" / HISTORY_FILENAME
