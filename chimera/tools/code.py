"""execute_code tool — run a Python snippet through the sandbox.

A clearer, dedicated interface than ``run_shell`` for the common "compute this / try this"
case: the agent passes source directly (no shell quoting), and it runs through the same
:mod:`chimera.sandbox` backend (local host or an isolated Docker container), so the
governance kernel and sandbox isolation apply exactly as they do to shell commands.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tools.base import Tool

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox
    from chimera.sandbox.confirm import HostExecConfirm

_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT = 30


class CodeInterpreterTool(Tool):
    """A *stateful* Python session: variables, imports and definitions persist across calls.

    Runs in-process (state persistence rules out a fresh subprocess each call), so it is
    powerful — use ``execute_code`` for isolated one-shots and the governance kernel to gate
    this when needed. Ideal for iterative data work: define once, build up, inspect.
    """

    name = "code_interpreter"
    description = (
        "Run Python in a persistent session — variables and imports persist across calls. "
        "Pass reset=true to clear the session. Runs in-process (not sandboxed)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to run in the session."},
            "reset": {"type": "boolean", "description": "Clear the session before running."},
        },
        "required": ["code"],
    }

    def __init__(self) -> None:
        self._namespace: dict[str, Any] = {}

    def run(self, **kwargs: Any) -> str:
        code = str(kwargs["code"])
        if kwargs.get("reset"):
            self._namespace.clear()
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                exec(compile(code, "<code_interpreter>", "exec"), self._namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001 - report any error as output, never crash
            out = f"{buffer.getvalue()}\n{type(exc).__name__}: {exc}".strip()
            return out[:_MAX_OUTPUT_CHARS]
        out = buffer.getvalue().strip()
        return (out or "(no output)")[:_MAX_OUTPUT_CHARS]


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

    def __init__(
        self,
        workspace: Path | None = None,
        sandbox: Sandbox | None = None,
        *,
        confirm: HostExecConfirm | None = None,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self._sandbox = sandbox
        self._confirm = confirm  # gate before host execution; None = run as before

    def run(self, **kwargs: Any) -> str:
        from chimera.sandbox import LocalSandbox

        code = str(kwargs["code"])
        timeout = int(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        sandbox = self._sandbox or LocalSandbox()
        if self._confirm is not None and not bool(
            getattr(sandbox, "is_isolated", lambda: False)()
        ):
            summary = code.strip().splitlines()[0][:120] if code.strip() else "(empty)"
            if not self._confirm(f"execute_code: {summary}"):
                return "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
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
