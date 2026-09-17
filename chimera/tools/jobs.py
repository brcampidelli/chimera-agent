"""The two tools beside a background job: what it is doing, and stopping it.

`run_shell` with `background: true` starts a job (`chimera/core/jobs.py`); these are how the agent
follows it. `job_status` reads — the state, the exit code, the tail of the log — and is in the
read-only set a step may run together; `job_cancel` kills the job's whole process tree and is not.
Neither takes a path or a command, so the kernel has nothing to judge on them beyond the tool name,
which is right: the command was judged when it was started.
"""

from __future__ import annotations

import time
from typing import Any

from chimera.core.jobs import TAIL_CHARS, JobRegistry
from chimera.tools.base import Tool


def _describe(job: Any, tail: str) -> str:
    since = time.time() - job.started_at
    head = f"job {job.id}: {job.state}"
    if job.exit_code is not None:
        head += f" (exit {job.exit_code})"
    elif job.state == "running":
        head += f" ({since:.0f}s so far)"
    lines = [head, f"command: {job.command}", f"cwd: {job.cwd}", f"log: {job.log}"]
    if job.state == "lost":
        lines.append(
            "lost: this process did not start it and its pid is gone — the exit code was never "
            "seen. The log holds whatever it wrote."
        )
    if tail.strip():
        lines.append(f"--- last {min(len(tail), TAIL_CHARS)} chars of output ---")
        lines.append(tail.rstrip())
    return "\n".join(lines)


class JobStatusTool(Tool):
    name = "job_status"
    description = (
        "What a background job started by run_shell(background=true) is doing: running, finished "
        "(with its exit code), cancelled or lost — and the tail of its output. Without a job_id, "
        "lists every job."
    )
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "The id run_shell returned. Omit to list all."},
        },
    }

    def __init__(self, jobs: JobRegistry) -> None:
        self._jobs = jobs

    def run(self, **kwargs: Any) -> str:
        job_id = str(kwargs.get("job_id") or "").strip()
        if not job_id:
            jobs = self._jobs.all()
            if not jobs:
                return "no background jobs"
            return "\n".join(
                f"job {j.id}: {j.state}"
                + (f" (exit {j.exit_code})" if j.exit_code is not None else "")
                + f" — {j.command[:100]}"
                for j in jobs
            )
        job = self._jobs.get(job_id)
        if job is None:
            return f"error: no such job {job_id!r}"
        return _describe(job, self._jobs.tail(job_id))


class JobCancelTool(Tool):
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

    def __init__(self, jobs: JobRegistry) -> None:
        self._jobs = jobs

    def run(self, **kwargs: Any) -> str:
        job_id = str(kwargs.get("job_id") or "").strip()
        job = self._jobs.cancel(job_id)
        if job is None:
            return f"error: no such job {job_id!r}"
        return _describe(job, self._jobs.tail(job_id, 1_000))
