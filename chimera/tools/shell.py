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
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)."},
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
        from chimera.sandbox import LocalSandbox
        from chimera.sandbox.local import _child_env

        if self._jobs is None:
            return (
                "error: background jobs are not available here — this registry has no job store. "
                "Run the command in the foreground."
            )
        if self._sandbox_is_isolated(sandbox) or not isinstance(sandbox, LocalSandbox):
            return (
                "error: background jobs run on the host sandbox only — an isolated sandbox runs a "
                "command to completion inside its container and has no detached form. Run it in "
                "the foreground, or set CHIMERA_SANDBOX=local."
            )
        argv, use_shell = sandbox._command_argv(command, cwd)
        try:
            job = self._jobs.start(command, cwd=cwd, env=_child_env(), argv=argv, shell=use_shell)
        except OSError as exc:
            return f"error: could not start the background job: {exc}"
        return (
            f"job {job.id} started in the background (pid {job.pid}); it keeps running after this "
            f"turn and is NOT stopped by cancelling the turn. Output: {job.log}. Check it with "
            f"job_status(job_id={job.id!r}); stop it with job_cancel(job_id={job.id!r}). "
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
        sandbox = self._sandbox or LocalSandbox()
        if (
            self._confirm is not None
            and not self._sandbox_is_isolated(sandbox)
            and not self._confirm(command)
        ):
            return "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
        if bool(kwargs.get("background", False)):
            return self._start_job(command, cwd, sandbox)
        result = sandbox.run(command, timeout=timeout, cwd=cwd)
        if result.timed_out:
            return f"error: command timed out after {timeout}s"
        out = result.output
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(out)} chars total]"
        return f"[exit {result.exit_code}]\n{out}".rstrip()
