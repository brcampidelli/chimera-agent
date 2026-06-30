"""Docker sandbox — runs commands inside an ephemeral, isolated container.

The workspace is bind-mounted at ``/workspace`` (writable) so file edits persist; the
container root filesystem is discarded on exit (``--rm``), memory is capped, and the
network is **disabled by default**. This is real isolation for agent-run commands.

When Docker is not available it degrades gracefully to a :class:`LocalSandbox`, so the
agent keeps working (just without container isolation) instead of failing outright.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from chimera.sandbox.base import Sandbox, SandboxResult
from chimera.sandbox.local import LocalSandbox
from chimera.telemetry import get_logger

_log = get_logger("sandbox.docker")


class DockerSandbox:
    def __init__(
        self,
        image: str = "python:3.12-slim",
        *,
        network: bool = False,
        memory: str = "512m",
        fallback: Sandbox | None = None,
    ) -> None:
        self.image = image
        self.network = network
        self.memory = memory
        self.fallback: Sandbox = fallback or LocalSandbox()
        self._available: bool | None = None

    def available(self) -> bool:
        """Whether the Docker CLI/daemon is reachable (checked once, then cached)."""
        if self._available is None:
            try:
                proc = subprocess.run(["docker", "version"], capture_output=True, timeout=10)
                self._available = proc.returncode == 0
            except (FileNotFoundError, OSError, subprocess.SubprocessError):
                self._available = False
        return self._available

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        if not self.available():
            _log.warning("docker unavailable; falling back to the local sandbox")
            return self.fallback.run(command, timeout=timeout, cwd=cwd)

        workdir = (Path(cwd) if cwd else Path.cwd()).resolve()
        argv = [
            "docker", "run", "--rm",
            "--network", "bridge" if self.network else "none",
            "--memory", self.memory,
            "-v", f"{workdir}:/workspace",
            "-w", "/workspace",
            self.image, "sh", "-c", command,
        ]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True
            )
        return SandboxResult(
            exit_code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or ""
        )
