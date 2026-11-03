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

import threading
import time
from collections.abc import Callable, Iterable
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
#: The least time between two answers about a job that is still running. Measured 2026-10-29 to 31:
#: the agent called `job_status` again and again with `wait_seconds` 0, each call answering at once,
#: so a run of identical calls burned one model step each. A repeat now answers no sooner than this
#: after the previous answer, unless the job ends first.
MIN_REPEAT_WAIT_SECONDS = 30.0


def answer_not_before(requested_until: float, last_answer_at: float | None) -> float:
    """The clock time before which a `job_status` answer for a running job is not given.

    The first answer about a job goes as soon as the requested wait allows. A repeat is held back
    until ``MIN_REPEAT_WAIT_SECONDS`` after the previous answer, even if the caller asked for less.
    """
    if last_answer_at is None:
        return requested_until
    return max(requested_until, last_answer_at + MIN_REPEAT_WAIT_SECONDS)


def process_tree_listening_ports(
    jobs: JobRegistry, workspace: Path, *, current_job_ids: set[str]
) -> frozenset[int]:
    """Ports currently listened to by a live process descended from this turn's workspace jobs."""
    try:
        import psutil
    except ImportError:
        return frozenset()

    root = workspace.resolve()
    ports: set[int] = set()
    for job_id in tuple(current_job_ids):
        job = jobs.get(job_id)
        if job is None or job.state != "running":
            continue
        try:
            cwd = Path(job.cwd).resolve()
            if cwd != root and not cwd.is_relative_to(root):
                continue
            process = psutil.Process(job.pid)
            descendants = [process, *process.children(recursive=True)]
        except (OSError, ValueError, AttributeError, psutil.Error):
            continue
        for proc in descendants:
            try:
                if not proc.is_running():
                    continue
                connections = getattr(proc, "net_connections", None) or proc.connections
                for conn in connections(kind="inet"):
                    if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                        continue
                    address = conn.laddr.ip
                    if address in {"127.0.0.1", "::1", "0.0.0.0", "::"}:
                        ports.add(conn.laddr.port)
            except (OSError, AttributeError, psutil.Error):
                continue
    return frozenset(ports)


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
        self,
        jobs: JobRegistry,
        workspace: Path | None = None,
        *,
        output_like: Tool | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(jobs, workspace)
        # The shell tool whose output this is: same bytes, same marker (see the module docstring).
        # Read once, here, the way every wrapper reads it (`GovernedTool`, the skill aliases).
        self.untrusted_output = output_like is not None and is_untrusted_output(output_like)
        # Injected so a test can run the wait on a fake clock; production uses the real one.
        self._clock = clock
        self._sleep = sleep
        # The turn's cancel signal, bound by the agent's turn (`bind_turn_cancel`); None = never.
        self._cancelled: Callable[[], bool] | None = None
        # When each job was last answered, by this tool: the spacing a repeat call must respect. A
        # lock, because calls in one parallel batch share this tool instance.
        self._answer_lock = threading.Lock()
        self._answered_at: dict[str, float] = {}

    def bind_turn_cancel(self, cancelled: Callable[[], bool]) -> None:
        """Let the wait stop when the turn is cancelled. Checked before every slice of the wait."""
        self._cancelled = cancelled

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
        began = self._clock()
        requested = _clamp(kwargs.get("wait_seconds"), 0, MAX_WAIT_SECONDS)
        with self._answer_lock:
            previous = self._answered_at.get(job_id)
            deadline = answer_not_before(began + requested, previous)
            if job.state == "running":
                # Reserved now, under the lock: a call that comes next in the same batch is held
                # after this one's answer, not after the time it started.
                self._answered_at[job_id] = deadline
        cancelled = False
        while job.state == "running" and (left := deadline - self._clock()) > 0:
            if self._cancelled is not None and self._cancelled():
                cancelled = True
                break
            self._sleep(min(_WAIT_POLL_SECONDS, left))
            job = self._find(job_id) or job
        now = self._clock()
        text = _describe(
            job,
            self._jobs,
            head_lines=_clamp(kwargs.get("head_lines"), 0, MAX_HEAD_LINES),
            tail_lines=_clamp(kwargs.get("tail_lines"), DEFAULT_TAIL_LINES, MAX_TAIL_LINES),
        )
        if cancelled:
            return text + (
                "\nturno cancelado: the wait stopped because the turn was cancelled. The job is still "
                "running; check it again in a later turn."
            )
        if job.state == "running" and previous is not None:
            # The job's start is wall-clock (it is stored on disk); the spacing is the injected clock.
            running_for = time.time() - job.started_at
            text += (
                f"\nStill running: {running_for:.0f}s since it started; {max(0.0, now - previous):.0f}s since "
                f"the last answer about it. A repeat is held until {MIN_REPEAT_WAIT_SECONDS:.0f}s "
                "after the previous answer, so calling again sooner does not help. Do something "
                "else meanwhile, or pass a larger wait_seconds."
            )
        return text


def bind_turn_cancel(tools: Iterable[Tool], cancelled: Callable[[], bool]) -> int:
    """Give every `job_status` in ``tools`` the turn's cancel signal, looking through wrappers.

    Governance wraps tools and keeps the wrapped one in ``inner``. Returns how many were bound, so a
    caller can tell a registry with no job tool from one whose tool was not reached.
    """
    bound = 0
    for tool in tools:
        current: object = tool
        for _ in range(8):  # a chain of wrappers, never a cycle; the bound is a fence, not a limit
            if isinstance(current, JobStatusTool):
                current.bind_turn_cancel(cancelled)
                bound += 1
                break
            current = getattr(current, "inner", None)
            if current is None:
                break
    return bound


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
