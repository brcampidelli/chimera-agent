"""execute_code tool — run a Python snippet through the sandbox.

A clearer, dedicated interface than ``run_shell`` for the common "compute this / try this"
case: the agent passes source directly (no shell quoting), and it runs through the same
:mod:`chimera.sandbox` backend (local host or an isolated Docker container), so the
governance kernel and sandbox isolation apply exactly as they do to shell commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tools.base import Tool

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox

_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT = 30


class ExecuteCodeTool(Tool):
    name = "execute_code"
    description = "Run a Python 3 code snippet in the sandbox and return its stdout/stderr."
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python 3 source to execute."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)."},
        },
        "required": ["code"],
    }

    def __init__(self, workspace: Path | None = None, sandbox: Sandbox | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self._sandbox = sandbox

    def run(self, **kwargs: Any) -> str:
        from chimera.sandbox import LocalSandbox

        code = str(kwargs["code"])
        timeout = int(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        sandbox = self._sandbox or LocalSandbox()
        script = self.workspace / f".chimera_exec_{os.getpid()}.py"
        try:
            script.write_text(code, encoding="utf-8")
            result = sandbox.run(f'python "{script.name}"', timeout=timeout, cwd=self.workspace)
        finally:
            script.unlink(missing_ok=True)
        if result.timed_out:
            return f"error: code timed out after {timeout}s"
        out = result.output
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(out)} chars total]"
        return f"[exit {result.exit_code}]\n{out}".rstrip()
