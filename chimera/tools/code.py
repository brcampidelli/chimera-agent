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
import shlex
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.sandbox.confirm import sandbox_is_isolated
from chimera.tools.base import Tool

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox
    from chimera.sandbox.confirm import HostExecConfirm

_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_TIMEOUT = 30

#: The names looked up on PATH when this process's own interpreter cannot run a script, in order.
#: ``python3`` first off Windows, where ``python`` is often absent (Debian and Ubuntu without
#: ``python-is-python3``, current macOS); ``python`` first on Windows, where ``python3`` is often the
#: Store's placeholder.
_PATH_PYTHONS = ("python", "py") if os.name == "nt" else ("python3", "python")


def _quote(path: str) -> str:
    return f'"{path}"' if os.name == "nt" else shlex.quote(path)


def python_command(sandbox: Sandbox, script_name: str) -> str | None:
    """The shell command that runs ``script_name`` with a Python the sandbox actually has.

    This was the literal ``python "<script>"``. Measured in ``bench/tool_loop_silent_failure``: on a
    WSL Ubuntu with ``python3`` and no ``python``, 743 of 748 failing calls answered
    ``python: not found``, so the tool never ran a line of code in two benches' worth of solves. The
    same holds on any machine without the alias.

    - **This machine** (the local sandbox and the OS sandbox, which wraps the same host): the
      interpreter running Chimera, by absolute path. It exists by construction, and it is the one
      ``code_interpreter`` already runs code in. A frozen build has no such interpreter — its
      ``sys.executable`` is the app — so PATH is searched instead; ``None`` when nothing is found.
    - **A container:** whichever of ``python3`` / ``python`` it has, resolved inside it.
    """
    from chimera.sandbox.docker import DockerSandbox
    from chimera.sandbox.local import LocalSandbox

    if isinstance(sandbox, DockerSandbox) and sandbox.available():
        return f'exec "$(command -v python3 || command -v python || echo python3)" {shlex.quote(script_name)}'
    if not isinstance(sandbox, LocalSandbox | DockerSandbox):
        # A sandbox of another kind resolves names in its own world, where a path from this machine
        # means nothing; it keeps the command it always had.
        return f'python "{script_name}"'
    # Here the command runs on this machine: the local sandbox, the OS sandbox, or a Docker sandbox
    # that fell back to local because the daemon is not there.
    if not getattr(sys, "frozen", False):
        return f"{_quote(sys.executable)} {_quote(script_name)}"
    found = next((p for p in (shutil.which(n) for n in _PATH_PYTHONS) if p), None)
    return f"{_quote(found)} {_quote(script_name)}" if found else None


class CodeInterpreterTool(Tool):
    """A *stateful* Python session: variables, imports and definitions persist across calls.

    Runs in-process (state persistence rules out a fresh subprocess each call), so it is powerful —
    and it **never** touches the sandbox: it is host execution by construction. It therefore honours
    ``CHIMERA_HOST_EXEC`` unconditionally (no ``is_isolated`` escape), because otherwise ``deny``
    would be trivially bypassable — the model would simply pick this tool over ``run_shell``.
    Ideal for iterative data work: define once, build up, inspect.
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

    def __init__(self, *, confirm: HostExecConfirm | None = None) -> None:
        self._namespace: dict[str, Any] = {}
        self._confirm = confirm  # gate before in-process host execution; None = run as before

    def run(self, **kwargs: Any) -> str:
        code = str(kwargs["code"])
        if self._confirm is not None:
            # No is_isolated() escape: exec() runs in THIS process, so it is always host execution.
            summary = code.strip().splitlines()[0][:120] if code.strip() else "(empty)"
            if not self._confirm(f"code_interpreter: {summary}"):
                return "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
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
    # "in the sandbox" read as "isolated from your machine", and the default sandbox is `local`,
    # whose own `is_isolated()` returns False and whose module docstring says outright that it is
    # not isolated. Read beside `code_interpreter`, which says "(not sandboxed)", the omission
    # implied the opposite of the truth. This is also the sentence the MODEL is shown before it
    # decides to call the tool, so it is the right place to say whose machine this runs on.
    description = (
        "Run a Python 3 code snippet and return its stdout/stderr. It runs in whatever sandbox is "
        "configured, which by default is THIS machine — not an isolated one."
    )
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
        if self._confirm is not None and not sandbox_is_isolated(sandbox):
            summary = code.strip().splitlines()[0][:120] if code.strip() else "(empty)"
            if not self._confirm(f"execute_code: {summary}"):
                return "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
        command = python_command(sandbox, f".chimera_exec_{os.getpid()}.py")
        if command is None:
            return (
                "error: no Python interpreter found on PATH (looked for "
                f"{', '.join(_PATH_PYTHONS)}). Not run."
            )
        script = self.workspace / f".chimera_exec_{os.getpid()}.py"
        try:
            script.write_text(code, encoding="utf-8")
            result = sandbox.run(command, timeout=timeout, cwd=self.workspace)
        finally:
            script.unlink(missing_ok=True)
        if result.timed_out:
            return f"error: code timed out after {timeout}s"
        out = result.output
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(out)} chars total]"
        return f"[exit {result.exit_code}]\n{out}".rstrip()
