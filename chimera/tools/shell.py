"""Shell execution tool.

Powerful by design: it runs commands in the workspace directory. For now safety
is limited to a timeout and the workspace cwd; the governance kernel (M5) will gate
it (allow/warn/block/review) and the sandbox layer (M3/M5) will isolate it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tools.base import Tool

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox

_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT = 60


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
        },
        "required": ["command"],
    }

    def __init__(self, workspace: Path | None = None, sandbox: Sandbox | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self._sandbox = sandbox

    def run(self, **kwargs: Any) -> str:
        from chimera.sandbox import LocalSandbox

        command = str(kwargs["command"])
        timeout = int(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        sandbox = self._sandbox or LocalSandbox()
        result = sandbox.run(command, timeout=timeout, cwd=self.workspace)
        if result.timed_out:
            return f"error: command timed out after {timeout}s"
        out = result.output
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(out)} chars total]"
        return f"[exit {result.exit_code}]\n{out}".rstrip()
