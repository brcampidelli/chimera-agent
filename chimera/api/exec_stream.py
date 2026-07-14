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
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.config import Settings

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

    mode = (settings.sandbox or "local").lower()
    if mode != "local":
        # Honor isolation: don't host-exec. Run one-shot in the configured sandbox and deliver the
        # whole output at once — no line streaming (that's the local-only path).
        from chimera.sandbox import get_sandbox

        result = get_sandbox(settings).run(command, timeout=int(max(1, timeout)), cwd=resolved)
        on_line(f"(sandbox={mode}: output shown on completion)")
        out = result.output
        if len(out) > _MAX_STREAM_CHARS:
            out = out[:_MAX_STREAM_CHARS] + "\n[output truncated]"
        if out:
            on_line(out)
        return result.exit_code

    from chimera.sandbox.local import _child_env

    posix = os.name == "posix"
    # start_new_session (POSIX) puts the child in its own process group so the watchdog can killpg the
    # whole tree — a forked grandchild can't survive the timeout holding the stdout pipe open.
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=resolved,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # MERGE stderr into stdout: a command-runner shows combined output
        text=True,
        bufsize=1,
        stdin=subprocess.DEVNULL,
        env=_child_env(),
        start_new_session=posix,
    )

    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        if posix:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined]
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
        else:
            proc.kill()

    watchdog = threading.Timer(timeout, _kill)
    watchdog.daemon = True
    watchdog.start()

    total = 0
    truncated = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:  # blocks per line; the watchdog kill closes the pipe → loop ends
            if truncated:
                continue  # keep draining so the child never blocks on a full pipe, but stop emitting
            total += len(line)
            if total > _MAX_STREAM_CHARS:
                truncated = True
                on_line("[output truncated]")
                continue
            on_line(line.rstrip("\n"))
    finally:
        proc.wait()
        watchdog.cancel()

    if timed_out.is_set():
        on_line(f"[timed out after {timeout:g}s]")
    return proc.returncode
