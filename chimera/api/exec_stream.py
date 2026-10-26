"""Streaming command-runner for the Code screen — an HONEST command-runner, NOT an interactive terminal.

Each call is a **fresh subprocess**: cwd/env do NOT persist between calls (no ``cd``/``export`` state).
On the local sandbox it streams the child's combined ``stdout``+``stderr`` line by line so the UI can
show live output; it is cross-platform (Windows + POSIX) because it never ``select``s on pipes — it
iterates the merged pipe and a watchdog ``Timer`` thread does the timeout kill (the whole read runs on
the caller's worker thread, so a blocking read is fine).

It HONORS the sandbox setting: when ``settings.sandbox != "local"`` it does NOT host-exec — it delegates
to :func:`~chimera.sandbox.get_sandbox` one-shot and delivers the whole output as a single block plus a
note, so ``CHIMERA_SANDBOX=docker`` stays isolated. Line-by-line streaming is the local path only.

The child env reuses :func:`~chimera.sandbox.local._child_env`, so provider secrets are scrubbed and the
non-interactive overrides + ``stdin=DEVNULL`` apply — the same posture as ``RunShellTool``.

**A run can be stopped, and stopping it means the whole tree.** Without that, closing the panel on a
``npm run dev`` leaves a dev server holding a port until the timeout — which for a long command is an
hour, and by then nobody remembers starting it. `Popen.kill()` alone is not enough either: a shell
command is usually a launcher, so killing the one process we hold orphans the ones doing the work.
The registry below is what a Stop button and a closed stream both reach for.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.proc.decode import console_line
from chimera.proc.stdio import kill_tree, no_window

if TYPE_CHECKING:
    from chimera.config import Settings

#: Commands running right now, by run id. A dict and not a single slot: the editor can start one
#: while another is still going, and a Stop button that killed "the current command" would kill the
#: wrong one exactly when two are running — which is the only time it matters.
_running: dict[str, subprocess.Popen[Any]] = {}
_running_lock = threading.Lock()


def cancel(run_id: str) -> bool:
    """Stop a running command and everything it started. False when there is nothing to stop.

    False is also the honest answer for a non-local sandbox: that path runs one-shot inside the
    sandbox and there is no host process here to signal. Reporting True would put a Stop button on
    screen that does nothing and says it worked.
    """
    with _running_lock:
        proc = _running.get(run_id)
    if proc is None or proc.poll() is not None:
        return False
    kill_tree(proc)
    return True


def running_count() -> int:
    """How many commands are live. For tests, and for a shutdown that wants to know."""
    with _running_lock:
        return sum(1 for proc in _running.values() if proc.poll() is None)


# Total streamed output cap: past this we emit "[output truncated]" once and stop emitting (still
# draining the pipe so the child never blocks on a full pipe), then return its real exit code.
_MAX_STREAM_CHARS = 200_000


def resolve_exec_cwd(workspace: Path, cwd: str) -> Path:
    """Resolve ``cwd`` under ``workspace`` (mirrors ``RunShellTool._resolve_cwd``); raise on escape.

    A blank ``cwd`` is the workspace root. Any ``..``/absolute path that resolves outside the workspace
    raises ``ValueError`` — the same guard the shell tool applies, so the command-runner can't escape.
    """
    root = workspace.resolve()
    if not cwd:
        return root
    candidate = (root / cwd).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError(f"cwd {cwd!r} escapes the workspace")
    return candidate


def run_streamed(
    command: str,
    *,
    workspace: Path,
    cwd: str = "",
    timeout: float = 60,
    on_line: Callable[[str], None],
    settings: Settings | None = None,
    run_id: str = "",
) -> int:
    """Run ``command`` as a fresh subprocess in the workspace, streaming output via ``on_line``.

    Returns the child's exit code. ``on_line`` is called once per output line (combined
    stdout+stderr). A ``cwd`` that escapes the workspace raises ``ValueError`` before anything runs.
    Non-local sandboxes are honored: the command runs one-shot inside the configured sandbox and its
    whole output is delivered as one block plus an honest note (never host-exec).
    """
    from chimera.config import get_settings

    settings = settings or get_settings()
    root = Path(workspace).resolve()
    resolved = resolve_exec_cwd(root, cwd)  # raises ValueError on escape — before any process starts

    # Decided from the RESOLVED backend, not from the setting string. Streaming spawns its own
    # process, which is equivalent to the configured sandbox only when that sandbox adds nothing to
    # a plain host process. `type(...) is` and not `isinstance`, deliberately: `OsSandbox` subclasses
    # `LocalSandbox` to reuse its process handling, and an `isinstance` check would stream — running
    # the command with this function's own Popen and quietly stepping around the kernel wrapper the
    # user is relying on.
    #
    # This branch used to read `settings.sandbox != "local"`. That was correct only while `local`
    # was the default; the day the default became `auto`, every machine took the one-shot path and
    # lost live output and Stop — which is how these lines came to be rewritten.
    from chimera.sandbox import get_sandbox
    from chimera.sandbox.local import LocalSandbox

    backend = get_sandbox(settings)
    from chimera.sandbox.local import _child_env
    from chimera.sandbox.os_sandbox import OsSandbox

    use_shell = False
    if type(backend) is OsSandbox:
        # Match OsSandbox.run exactly: one wrapper builder, including its shell flag if
        # the wrapper is unavailable. The subprocess remains registered for live output and cancel.
        process_argv, use_shell = backend.command_argv(command, resolved)
        on_line("(sandbox=os: output shown on completion)")
    elif type(backend) is LocalSandbox:
        process_argv = command
        use_shell = True
    else:
        # Docker runs the process inside the daemon/container rather than as this Popen's children,
        # so killing the client does not reliably stop the container command.
        result = backend.run(command, timeout=int(max(1, timeout)), cwd=resolved)
        out = result.output
        on_line("(sandbox=docker: output shown on completion)")
        if len(out) > _MAX_STREAM_CHARS:
            out = out[:_MAX_STREAM_CHARS] + "\n[output truncated]"
        if out:
            on_line(out)
        return result.exit_code

    posix = os.name == "posix"
    # start_new_session (POSIX) puts the child in its own process group so the watchdog can killpg the
    # whole tree — a forked grandchild can't survive the timeout holding the stdout pipe open.
    if use_shell:
        # Keep shell=True explicit at the one named streaming spawn site in the shell allowlist.
        proc = subprocess.Popen(
            process_argv,
            shell=True,
            cwd=resolved,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # MERGE stderr into stdout: a command-runner shows combined output
            text=True,
            # Named and named this way for a reason particular to streaming. `text=True` alone reads
            # with the ANSI code page while a console program writes the OEM one.
            encoding="utf-8",
            errors="surrogateescape",
            bufsize=1,
            stdin=subprocess.DEVNULL,
            env=_child_env(),
            start_new_session=posix,
            **no_window(),
        )
    else:
        proc = subprocess.Popen(
            process_argv,
            cwd=resolved,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            bufsize=1,
            stdin=subprocess.DEVNULL,
            env=_child_env(),
            start_new_session=posix,
            **no_window(),
        )

    if run_id:
        with _running_lock:
            _running[run_id] = proc

    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        kill_tree(proc)

    watchdog = threading.Timer(timeout, _kill)
    watchdog.daemon = True
    watchdog.start()

    total = 0
    truncated = False
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:  # blocks per line; the watchdog kill closes the pipe → loop ends
            if truncated:
                continue  # keep draining so the child never blocks on a full pipe, but stop emitting
            line = console_line(raw_line)
            # Decoded before counting, so `_MAX_STREAM_CHARS` keeps meaning characters. Counting the
            # bytes instead would truncate a CJK or emoji-heavy log at a third of the stated budget.
            total += len(line)
            if total > _MAX_STREAM_CHARS:
                truncated = True
                on_line("[output truncated]")
                continue
            on_line(line.rstrip("\n"))
    finally:
        proc.wait()
        watchdog.cancel()
        if run_id:
            with _running_lock:
                _running.pop(run_id, None)

    if timed_out.is_set():
        on_line(f"[timed out after {timeout:g}s]")
    return proc.returncode
