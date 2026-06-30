"""Local sandbox — runs commands directly on the host (timeout + working dir only).

The default backend and the fallback when Docker is unavailable. It is *not* isolated;
the governance kernel gates what reaches it, and DockerSandbox provides real isolation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from chimera.sandbox.base import SandboxResult


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
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True
            )
        return SandboxResult(
            exit_code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or ""
        )
