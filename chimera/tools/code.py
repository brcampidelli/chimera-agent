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
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from chimera.sandbox.confirm import sandbox_is_isolated
from chimera.tools.base import Tool
from chimera.tools.clip import clip_output, keep_tail_enabled
from chimera.tools.workspace import queue_refusal

if TYPE_CHECKING:
    from chimera.sandbox.base import Sandbox
    from chimera.sandbox.confirm import HostExecConfirm

_MAX_OUTPUT_CHARS = 20_000
#: The most of ``code_interpreter``'s output an exception line may take. It is kept whole below this
#: and clipped above it, so a ``ValueError`` carrying a 50k repr cannot push the printed output out.
_MAX_EXC_LINE_CHARS = 2_000
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
    found, _source = host_python()
    return f"{_quote(found)} {_quote(script_name)}" if found else None


#: How :func:`host_python` found the interpreter: this process's own, one on PATH (a frozen build,
#: which has no interpreter of its own), or none at all.
HostPythonSource = Literal["interpreter", "path", "missing"]


def host_python() -> tuple[str | None, HostPythonSource]:
    """The Python that runs ``execute_code`` when the command runs on THIS machine, and how it was found.

    One function for the tool and for ``doctor``, so the screen that answers "which Python?" cannot
    name a different one from the one the tool then runs. The question needs asking at all because
    of the frozen desktop build: there ``sys.executable`` is the app, so the interpreter is whatever
    PATH happens to hold — or nothing — and a snippet that fails because none was found reads, in a
    transcript, exactly like a model that wrote bad code.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable, "interpreter"
    found = next((p for p in (_runnable_on_path(n) for n in _PATH_PYTHONS) if p), None)
    return (found, "path") if found else (None, "missing")


#: A path inside the directory where Windows keeps its App Execution Aliases. Matched on the text,
#: either separator, so the rule reads the same in a test on Linux as on the machine it is about.
_ALIAS_DIR = re.compile(r"[\\/]WindowsApps[\\/][^\\/]+$", re.IGNORECASE)

#: Seconds a candidate alias gets to start and exit. A real interpreter answers ``import sys`` in a
#: fraction of that; the placeholder answers at once with its exit code.
_ALIAS_PROBE_TIMEOUT = 5

#: What each alias answered, keyed by its path and the alias file's own mtime, so installing Python
#: (which rewrites the alias) is noticed without a restart, and ``doctor`` — the app's heartbeat —
#: does not start a process on every tick.
_alias_verdicts: dict[tuple[str, int], bool] = {}


def _runnable_on_path(name: str) -> str | None:
    """``name`` on PATH, skipping a Windows App Execution Alias that does not run Python.

    A clean Windows has ``python.exe`` and ``python3.exe`` under ``%LOCALAPPDATA%`` in
    ``Microsoft/WindowsApps`` before any Python is installed: zero-byte aliases of the App Installer that print "Python was
    not found" and exit 9009. ``shutil.which`` finds them, so a frozen build on exactly the machine
    the Python row exists to explain would have named the placeholder as its interpreter, and the
    screen would have said Python was there while every snippet failed to start.

    Not every alias there is a placeholder: the Store's Python and the Python install manager put a
    real interpreter behind the same kind of zero-byte link, in the same directory. So an alias is
    tried once, and kept only if it runs; when it does not, the rest of PATH is still searched.
    """
    found = shutil.which(name)
    alias = _ALIAS_DIR.search(found) if found else None
    if not found or alias is None or _alias_runs(found):
        return found
    alias_dir = os.path.normcase(found[: alias.start() + len("/WindowsApps")])
    path = os.environ.get("PATH", "").split(os.pathsep)
    rest = [d for d in path if d and os.path.normcase(d.rstrip("\\/")) != alias_dir]
    return shutil.which(name, path=os.pathsep.join(rest)) if rest else None


def _alias_runs(path: str) -> bool:
    """Whether the alias at ``path`` starts a Python that exits cleanly. Cached per alias file."""
    try:
        key = (path, os.lstat(path).st_mtime_ns)
    except OSError:
        return False
    if key not in _alias_verdicts:
        try:
            done = subprocess.run(
                [path, "-c", "import sys"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_ALIAS_PROBE_TIMEOUT,
                # No console window flashing up behind the app each time the alias is tried.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            _alias_verdicts[key] = done.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _alias_verdicts[key] = False
    return _alias_verdicts[key]


def host_python_report() -> dict[str, object]:
    """``doctor``'s answer to "which Python does execute_code use here?", measured on this machine.

    Only the host half. Whether a container answers instead is the sandbox's question, already asked
    live by ``GET /api/governance/sandbox``; probing Docker here would put a subprocess with a ten
    second timeout behind ``doctor``, which the app polls as its heartbeat.
    """
    path, source = host_python()
    return {
        "path": path or "",
        "source": source,
        "frozen": bool(getattr(sys, "frozen", False)),
        # The names a frozen build looks for, so "missing" can say what was looked for.
        "looked_for": list(_PATH_PYTHONS),
    }


def _restore_cwd(path: str) -> None:
    """Put the process back in ``path``; if the code deleted that folder, stay where it is."""
    if os.getcwd() != path:
        with contextlib.suppress(OSError):
            os.chdir(path)


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
        # In THIS process: the queue's own functions are an import away, not only its folder.
        fenced = queue_refusal(self.name, code, Path.cwd())
        if fenced is not None:
            return fenced
        if self._confirm is not None:
            # No is_isolated() escape: exec() runs in THIS process, so it is always host execution.
            summary = code.strip().splitlines()[0][:120] if code.strip() else "(empty)"
            if not self._confirm(f"code_interpreter: {summary}"):
                return "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
        if kwargs.get("reset"):
            self._namespace.clear()
        buffer = io.StringIO()
        # The working directory is PROCESS state, and this process is the app's backend: it resolves
        # its `.env` (the keys, and the file the own-files fence protects) and a relative data folder
        # against it. An `os.chdir` here used to outlive the call — on 2026-10-06 an installed
        # backend started in the install folder was found standing in a project folder, where the
        # `.env` fence guarded a file that does not exist and the bridge refused the project as "the
        # app's own data". The program may move around while it runs; the process is put back where
        # it was, whatever the code did or raised.
        cwd_before = os.getcwd()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                exec(compile(code, "<code_interpreter>", "exec"), self._namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001 - report any error as output, never crash
            # This was `out[:_MAX_OUTPUT_CHARS]`, with no marker: the only tool that cut its output
            # in silence, so 20 000 characters read as a complete answer. Clipping the whole
            # `buffer + exception` head-only then cut the other way: the marker said "clipped" and
            # the exception line, last by construction, was the part that went, so the model was
            # never told the code had raised. The exception line is clipped on its own (a huge
            # message must not crowd out the buffer) and always kept; the printed output gets the
            # room that is left, head-only. Under the cap the result is byte-identical to before.
            exc_line = clip_output(f"{type(exc).__name__}: {exc}", _MAX_EXC_LINE_CHARS)
            room = max(_MAX_OUTPUT_CHARS - len(exc_line) - 1, 0)
            printed = clip_output(buffer.getvalue(), room)
            return f"{printed}\n{exc_line}".strip()
        finally:
            _restore_cwd(cwd_before)
        out = buffer.getvalue().strip()
        return clip_output(out or "(no output)", _MAX_OUTPUT_CHARS)


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
        fenced = queue_refusal(self.name, code, self.workspace)
        if fenced is not None:
            return fenced
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
        # The verdict of a command is at its end (pytest's FAILED summary, a traceback's last line);
        # keeping it is opt-in until measured — see chimera.tools.clip.
        out = clip_output(result.output, _MAX_OUTPUT_CHARS, keep_tail=keep_tail_enabled())
        return f"[exit {result.exit_code}]\n{out}".rstrip()
