"""Shell execution tool.

Powerful by design: it runs commands in the workspace directory. For now safety
is limited to a timeout and the workspace cwd; the governance kernel (M5) will gate
it (allow/warn/block/review) and the sandbox layer (M3/M5) will isolate it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.sandbox.confirm import sandbox_is_isolated
from chimera.tools.base import Tool
from chimera.tools.clip import clip_output, keep_tail_enabled
from chimera.tools.workspace import queue_refusal

if TYPE_CHECKING:
    from chimera.core.jobs import JobRegistry
    from chimera.sandbox.base import Sandbox
    from chimera.sandbox.confirm import HostExecConfirm

_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 3600  # cap: long ops (backups, builds) are fine; runaway ones are not


class RunShellTool(Tool):
    name = "run_shell"
    description = (
        "Run a shell command in the workspace directory and return its output. "
        "Use with care: this can modify the system."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in seconds (default 60). A command that may take longer than a few "
                    "minutes (a benchmark, a long build or test run) should be started with "
                    "background=true instead of a long timeout."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Working directory, relative to the workspace (default: workspace root).",
            },
            "background": {
                "type": "boolean",
                "description": (
                    "Start the command as a background job and return at once with its job id, "
                    "instead of waiting for it. For long work (downloads, builds, batch "
                    "processing) that should not hold the conversation. The job keeps running "
                    "after this turn; check it with job_status, stop it with job_cancel."
                ),
            },
        },
        "required": ["command"],
    }

    def __init__(
        self,
        workspace: Path | None = None,
        sandbox: Sandbox | None = None,
        *,
        default_timeout: int = _DEFAULT_TIMEOUT,
        max_timeout: int = _MAX_TIMEOUT,
        confirm: HostExecConfirm | None = None,
        jobs: JobRegistry | None = None,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self._sandbox = sandbox
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        # Optional gate consulted before running on the host; None = run as before (isolated sandbox,
        # explicit allow, or a caller that opts out). See chimera.sandbox.confirm.
        self._confirm = confirm
        # Where `background: true` puts a command. None = the parameter is refused with a sentence,
        # which is how a registry built without a home (a bench, a bare `RunShellTool()`) behaves.
        self._jobs = jobs

    _sandbox_is_isolated = staticmethod(sandbox_is_isolated)  # shared with code.py; see confirm.py

    def _start_job(self, command: str, cwd: Path, sandbox: Any) -> str:
        """The `background: true` path, reached only AFTER every gate the foreground path passes:
        the kernel and the taint ledger saw this exact call one wrapper out, and the host-exec
        confirm above said yes. What differs is only that nobody waits."""
        from chimera.core.jobs import JobLimitError
        from chimera.sandbox import LocalSandbox
        from chimera.sandbox.local import _child_env

        if self._jobs is None:
            return (
                "error: background jobs are not available here — this registry has no job store. "
                "Run the command in the foreground."
            )
        # The local family only: the host, or a kernel sandbox (bubblewrap, Seatbelt) that is the
        # same process wrapped in an argv — `_command_argv` below applies that wrapper to the job
        # exactly as `run` applies it to a foreground command. A container is another thing: it runs
        # a command to completion inside itself, and there is no detached form of that here.
        if not isinstance(sandbox, LocalSandbox):
            return (
                "error: background jobs run on the host sandbox only — an isolated container runs a "
                "command to completion inside itself and has no detached form. Run it in the "
                "foreground, or set CHIMERA_SANDBOX=local."
            )
        argv, use_shell = sandbox._command_argv(command, cwd)
        # Said BEFORE the start, while the count is still the one the person would want to know: a
        # third job running is fine, and the tenth is worth a sentence, but neither is a refusal.
        over = self._jobs.over_advisory_limit()
        try:
            job = self._jobs.start(command, cwd=cwd, env=_child_env(), argv=argv, shell=use_shell)
        except JobLimitError as exc:
            running = "; ".join(f"{j.id}: {j.command[:80]}" for j in exc.running)
            return (
                f"error: {len(exc.running)} background jobs are already running, the most this app "
                f"runs at once ({exc.limit}; CHIMERA_JOBS_MAX_RUNNING). Not started. Wait for one to "
                f"finish (job_status) or stop one (job_cancel). Running: {running}"
            )
        except OSError as exc:
            return f"error: could not start the background job: {exc}"
        limit = f" It is stopped after {job.max_runtime:.0f}s at most." if job.max_runtime else ""
        if over is not None:
            limit += (
                f" Note: {over[0]} other background jobs were already running (more than the usual "
                f"{over[1]}); that is a suggestion, not a limit, and it started anyway."
            )
        return (
            f"job {job.id} started in the background (pid {job.pid}); it keeps running after this "
            f"turn and is NOT stopped by cancelling the turn.{limit} Check it with "
            f"job_status(job_id={job.id!r}) — it shows the state, the exit code and the last lines "
            f"of output; stop it with job_cancel(job_id={job.id!r}). "
            "Do not report the work as done until job_status says it finished."
        )

    def _resolve_cwd(self, rel: str | None) -> Path | str:
        """Resolve a per-call ``cwd`` under the workspace, or an ``error:`` string if it escapes."""
        if not rel:
            return self.workspace
        candidate = (self.workspace / rel).resolve()
        if candidate != self.workspace and not candidate.is_relative_to(self.workspace):
            return f"error: cwd '{rel}' escapes the workspace"
        return candidate

    def run(self, **kwargs: Any) -> str:
        from chimera.sandbox import LocalSandbox

        command = str(kwargs["command"])
        timeout = int(kwargs.get("timeout") or self.default_timeout)
        timeout = max(1, min(timeout, self.max_timeout))
        cwd = self._resolve_cwd(kwargs.get("cwd"))
        if isinstance(cwd, str):  # escape error
            return cwd
        # Before the host-exec question and before anything runs, in every sandbox: an isolated
        # container may still mount the data folder, and a command that answers a question must not
        # become a question the person is asked about instead.
        fenced = queue_refusal(self.name, command, cwd)
        if fenced is not None:
            return fenced
        sandbox = self._sandbox or LocalSandbox()
        if (
            self._confirm is not None
            and not self._sandbox_is_isolated(sandbox)
            and not self._confirm(command)
        ):
            return "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
        if bool(kwargs.get("background", False)):
            return self._start_job(command, cwd, sandbox)
        if self._jobs is not None and isinstance(sandbox, LocalSandbox):
            jobs = self._jobs

            def adopt(proc: Any) -> str | None:
                job = jobs.adopt(proc, command, cwd=cwd)
                return job.id if job is not None else None

            result = sandbox.run(command, timeout=timeout, cwd=cwd, on_timeout=adopt)
        else:
            result = sandbox.run(command, timeout=timeout, cwd=cwd)
        if result.adopted:
            return (
                f"job {result.adopted} is still running after {timeout}s and was NOT stopped: it "
                f"continues in the background and keeps running after this turn. Its output appears "
                f"when it finishes; check it with job_status(job_id={result.adopted!r}), stop it with "
                f"job_cancel(job_id={result.adopted!r}). Do not report the work as done until "
                "job_status says it finished."
            )
        if result.timed_out:
            if self._jobs is not None and isinstance(sandbox, LocalSandbox):
                return (
                    f"error: command timed out after {timeout}s and was stopped. If it needs longer, "
                    "run it again with background=true: it keeps running after this turn, and "
                    "job_status shows how it is going."
                )
            return f"error: command timed out after {timeout}s"
        # The verdict of a command is at its end (pytest's FAILED summary, a traceback's last line);
        # keeping it is opt-in until measured — see chimera.tools.clip.
        out = clip_output(result.output, _MAX_OUTPUT_CHARS, keep_tail=keep_tail_enabled())
        return f"[exit {result.exit_code}]\n{out}".rstrip()
