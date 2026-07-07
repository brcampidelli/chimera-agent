"""Local sandbox — runs commands directly on the host (timeout + working dir only).

The default backend and the fallback when Docker is unavailable. It is *not* isolated;
the governance kernel gates what reaches it, and DockerSandbox provides real isolation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from chimera.sandbox.base import SandboxResult

# Non-interactive execution env — the single biggest cause of an autonomous agent "hanging" is a
# command that blocks on input it will never get: git opening an editor/pager or asking for
# credentials, apt/read waiting on stdin, an accidental REPL. Combined with stdin=DEVNULL (so a read
# gets EOF instead of blocking), these turn every such stall into an instant, bounded result instead
# of burning the whole per-command timeout. A correctness fix, not just speed — it's how a single
# hard task could eat a 600s budget one 60s-timeout at a time.
_NONINTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",  # git never prompts for credentials
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_EDITOR": "true",  # `git commit`/`rebase` never opens $EDITOR and blocks
    "EDITOR": "true",
    "DEBIAN_FRONTEND": "noninteractive",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PYTHONUNBUFFERED": "1",
    "CI": "1",
}


class LocalSandbox:
    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **_NONINTERACTIVE_ENV},
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True
            )
        return SandboxResult(
            exit_code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or ""
        )
