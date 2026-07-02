"""Shell execution tool.

Powerful by design: it runs commands in a working directory. For now safety
is limited to a timeout and the cwd; the governance kernel (M5) will gate
it (allow/warn/block/review) and the sandbox layer (M3/M5) will isolate it.

Defaults are tunable via the environment so a scheduled/agent run can execute
longer scripts and resolve paths from a fixed root:

- ``CHIMERA_SHELL_TIMEOUT``    default per-command timeout in seconds (default 180)
- ``CHIMERA_SHELL_MAX_OUTPUT`` max characters of output kept (default 40000)
- ``CHIMERA_SHELL_CWD``        default working directory when no workspace is given
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tools.base import Tool

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


_MAX_OUTPUT_CHARS = _env_int("CHIMERA_SHELL_MAX_OUTPUT", 40_000)
_DEFAULT_TIMEOUT = _env_int("CHIMERA_SHELL_TIMEOUT", 180)


class RunShellTool(Tool):
    name = "run_shell"
    description = (
        "Run a shell command and return its output. Optionally set 'cwd' and a "
        "longer 'timeout' for slow scripts. Use with care: this can modify the system."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default {_DEFAULT_TIMEOUT}).",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory to run in (defaults to the workspace).",
            },
        },
        "required": ["command"],
    }

    def __init__(self, workspace: Path | None = None, sandbox: Sandbox | None = None) -> None:
        env_cwd = os.environ.get("CHIMERA_SHELL_CWD")
        base = workspace or (Path(env_cwd) if env_cwd else Path.cwd())
        self.workspace = base.resolve()
        self._sandbox = sandbox

    def run(self, **kwargs: Any) -> str:
        from chimera.sandbox import LocalSandbox

        command = str(kwargs["command"])
        timeout = int(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        cwd_arg = kwargs.get("cwd")
        cwd = Path(cwd_arg).resolve() if cwd_arg else self.workspace
        sandbox = self._sandbox or LocalSandbox()
        result = sandbox.run(command, timeout=timeout, cwd=cwd)
        if result.timed_out:
            return f"error: command timed out after {timeout}s"
        out = result.output
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(out)} chars total]"
        return f"[exit {result.exit_code}]\n{out}".rstrip()
