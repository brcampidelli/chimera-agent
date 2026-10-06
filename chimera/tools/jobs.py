"""The two tools beside a background job: what it is doing, and stopping it.

`run_shell` with `background: true` starts a job (`chimera/core/jobs.py`); these are how the agent
follows it. `job_status` reads — the state, the exit code, the start and end times, and a bounded
slice of the log (its first and last lines, never the whole file) — and is in the read-only set a
step may run together; `job_cancel` kills the job's whole process tree and is not. Neither takes a
path or a command, so the kernel has nothing to judge on them beyond the tool name, which is right:
the command was judged when it was started.

Two properties that keep them inside the same fences as `run_shell`:

- **The workspace jail.** Given a workspace (the registry always gives one), a job is visible to
  these tools only when it was started inside that workspace. The job store is per home, so without
  this an agent working in one project could read another project's output or stop its build. The
  owner's views (the API, the bridge, the app) see every job.
- **The output is treated as `run_shell`'s output is.** What `job_status` returns is what the command
  printed, the same bytes `run_shell` would have returned had it waited. So it carries the same
  untrusted-output marker as the shell tool it follows (:func:`chimera.tools.base.is_untrusted_output`
  on that tool): whatever fencing the taint ledger applies to one, it applies to the other, and a
  deployment that marks its shell output untrusted cannot have it laundered through a status read.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from chimera.core.jobs import MAX_HEAD_LINES, MAX_TAIL_LINES, Job, JobRegistry
from chimera.tools.base import Tool, is_untrusted_output

#: Lines of log `job_status` shows when it is not told how many.
DEFAULT_TAIL_LINES = 40
#: The longest `job_status` will wait for a running job to end in one call, and how often it looks.
#: Without a wait the model's only way to follow a job was to call again: measured 2026-10-06, an
#: agent following a 20-minute test run called job_status 75 times in 2.5 minutes, one model step
#: each, varying `tail_lines` so the identical-call loop detector never fired.
MAX_WAIT_SECONDS = 120
_WAIT_POLL_SECONDS = 1.0


def _when(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def _describe(job: Job, jobs: JobRegistry, *, head_lines: int = 0, tail_lines: int = 0) -> str:
    since = (job.finished_at or time.time()) - job.started_at
    head = f"job {job.id}: {job.state}"
    if job.exit_code is not None:
        head += f" (exit {job.exit_code})"
    if job.state == "running":
        head += f" ({since:.0f}s so far"
        if job.max_runtime:
            head += f"; stopped at {job.max_runtime:.0f}s"
        head += ")"
    lines = [
        head,
        f"command: {job.command}",
        f"cwd: {job.cwd}",
        f"started: {_when(job.started_at)}   ended: {_when(job.finished_at)}   ({since:.0f}s)",
    ]
    if job.state == "timed_out":
        lines.append(
            f"timed_out: it reached the maximum runtime ({job.max_runtime:.0f}s) and was killed with "
            "everything it started."
        )
    if job.state == "lost":
        alive = bool(job.extra.get("pid_alive_when_lost"))
        lines.append(
            "lost: the process that started and watched it is gone (the app restarted), so its exit "
            "code was never seen. The log holds whatever it wrote."
            + (
                f" Its pid {job.pid} was still alive when this was noticed; it cannot be stopped "
                "from here, because a pid alone may by now belong to another program."
                if alive
                else ""
            )
        )
    if job.extra.get("ended_by") == "app_exit":
        lines.append("cancelled because the app closed while it ran.")
    if job.extra.get("cancel_refused"):
        lines.append(f"not stopped: {job.extra['cancel_refused']}.")
    part = jobs.read_log(job.id, head_lines=head_lines, tail_lines=tail_lines)
    if part is not None and (head_lines or tail_lines):
        lines.append(f"log: {part.size} bytes")
        if part.head:
            lines.append(f"--- first {len(part.head.splitlines())} lines ---")
            lines.append(part.head.rstrip())
        if part.gap:
            lines.append("--- ... ---" if part.head else "--- (earlier output not shown) ---")
        if part.tail:
            label = "last" if part.gap else "all"
            lines.append(f"--- {label} {len(part.tail.splitlines())} lines ---")
            lines.append(part.tail.rstrip())
    return "\n".join(lines)


def _clamp(value: Any, default: int, ceiling: int) -> int:
    try:
        number = int(value) if value is not None and value != "" else default
    except (TypeError, ValueError):
        number = default
    return max(0, min(number, ceiling))


class _JobTool(Tool):
    def __init__(self, jobs: JobRegistry, workspace: Path | None = None) -> None:
        self._jobs = jobs
        self._workspace = workspace.resolve() if workspace is not None else None

    def _visible(self, job: Job) -> bool:
        """Inside this tool's workspace — the same test `run_shell` applies to its ``cwd``."""
        if self._workspace is None:
            return True
        try:
            cwd = Path(job.cwd).resolve()
        except (OSError, ValueError):
            return False
        return cwd == self._workspace or cwd.is_relative_to(self._workspace)

    def _find(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id) if job_id else None
        return job if job is not None and self._visible(job) else None


class JobStatusTool(_JobTool):
    name = "job_status"
    description = (
        "What a background job started by run_shell(background=true) is doing: running, finished "
        "(with its exit code), cancelled, timed_out or lost — its start and end times, and the last "
        "lines of its output (tail_lines, default 40; head_lines for the first ones). Without a "
        "job_id, lists this workspace's jobs. To wait for a running job, pass wait_seconds (up to "
        f"{MAX_WAIT_SECONDS}): the call returns as soon as the job ends, or when the wait runs out — "
        "one call instead of asking again and again."
    )
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "The id run_shell returned. Omit to list all."},
            "tail_lines": {
                "type": "integer",
                "description": f"Lines from the end of the output (default {DEFAULT_TAIL_LINES}, "
                f"max {MAX_TAIL_LINES}).",
            },
            "head_lines": {
                "type": "integer",
                "description": f"Lines from the start of the output (default 0, max {MAX_HEAD_LINES}).",
            },
            "wait_seconds": {
                "type": "integer",
                "description": f"Wait up to this long for a running job to end (default 0, max "
                f"{MAX_WAIT_SECONDS}).",
            },
        },
    }

    def __init__(
        self, jobs: JobRegistry, workspace: Path | None = None, *, output_like: Tool | None = None
    ) -> None:
        super().__init__(jobs, workspace)
        # The shell tool whose output this is: same bytes, same marker (see the module docstring).
        # Read once, here, the way every wrapper reads it (`GovernedTool`, the skill aliases).
        self.untrusted_output = output_like is not None and is_untrusted_output(output_like)

    def run(self, **kwargs: Any) -> str:
        job_id = str(kwargs.get("job_id") or "").strip()
        if not job_id:
            jobs = [j for j in self._jobs.all() if self._visible(j)]
            if not jobs:
                return "no background jobs"
            return "\n".join(
                f"job {j.id}: {j.state}"
                + (f" (exit {j.exit_code})" if j.exit_code is not None else "")
                + f" — started {_when(j.started_at)} — {j.command[:100]}"
                for j in jobs
            )
        job = self._find(job_id)
        if job is None:
            return f"error: no such job {job_id!r}"
        deadline = time.monotonic() + _clamp(kwargs.get("wait_seconds"), 0, MAX_WAIT_SECONDS)
        while job.state == "running" and (left := deadline - time.monotonic()) > 0:
            time.sleep(min(_WAIT_POLL_SECONDS, left))
            job = self._find(job_id) or job
        return _describe(
            job,
            self._jobs,
            head_lines=_clamp(kwargs.get("head_lines"), 0, MAX_HEAD_LINES),
            tail_lines=_clamp(kwargs.get("tail_lines"), DEFAULT_TAIL_LINES, MAX_TAIL_LINES),
        )


class JobCancelTool(_JobTool):
    name = "job_cancel"
    description = (
        "Stop a background job started by run_shell(background=true): kills the command and "
        "everything it started. A job that already ended is reported as it is."
    )
    parameters = {
        "type": "object",
        "properties": {"job_id": {"type": "string", "description": "The id run_shell returned."}},
        "required": ["job_id"],
    }

    def run(self, **kwargs: Any) -> str:
        job_id = str(kwargs.get("job_id") or "").strip()
        if self._find(job_id) is None:
            return f"error: no such job {job_id!r}"
        job = self._jobs.cancel(job_id)
        if job is None:
            return f"error: no such job {job_id!r}"
        return _describe(job, self._jobs, tail_lines=20)
