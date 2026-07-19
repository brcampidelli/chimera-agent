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
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self._sandbox = sandbox
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        # Optional gate consulted before running on the host; None = run as before (isolated sandbox,
        # explicit allow, or a caller that opts out). See chimera.sandbox.confirm.
        self._confirm = confirm

    _sandbox_is_isolated = staticmethod(sandbox_is_isolated)  # shared with code.py; see confirm.py

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
        result = sandbox.run(command, timeout=timeout, cwd=cwd)
        if result.timed_out:
            return f"error: command timed out after {timeout}s"
        out = result.output
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(out)} chars total]"
        return f"[exit {result.exit_code}]\n{out}".rstrip()
