"""Docker sandbox — runs commands inside an ephemeral, isolated container.

The workspace is bind-mounted at ``/workspace`` (writable) so file edits persist; the
container root filesystem is discarded on exit (``--rm``), memory is capped, and the
network is **disabled by default**. This is real isolation for agent-run commands.

An optional OCI ``runtime`` (e.g. ``runsc`` for gVisor) can be set to harden the boundary:
gVisor interposes a userspace kernel that intercepts the container's syscalls, shrinking
the host-kernel attack surface a plain ``runc`` container leaves exposed — a drop-in step
below a full microVM. Default is empty (the daemon's default runtime, ``runc``).

When Docker is not available it degrades gracefully to a :class:`LocalSandbox`, so the
agent keeps working (just without container isolation) instead of failing outright.
"""

from __future__ import annotations

import subprocess
from contextlib import suppress
from pathlib import Path

from chimera.sandbox.base import Sandbox, SandboxResult
from chimera.sandbox.local import _NONINTERACTIVE_ENV, LocalSandbox
from chimera.telemetry import get_logger

_log = get_logger("sandbox.docker")


class DockerSandbox:
    def __init__(
        self,
        image: str = "python:3.12-slim",
        *,
        network: bool = False,
        memory: str = "512m",
        runtime: str | None = None,
        fallback: Sandbox | None = None,
    ) -> None:
        self.image = image
        self.network = network
        self.memory = memory
        # Optional OCI runtime (e.g. "runsc" for gVisor); empty/None uses the daemon default.
        self.runtime = (runtime or "").strip() or None
        self.fallback: Sandbox = fallback or LocalSandbox()
        self._available: bool | None = None

    def is_isolated(self) -> bool:
        """Isolated only when Docker is actually reachable; otherwise this falls back to the host."""
        return self.available()

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
        # No `-i`/`-t`: the container's stdin stays closed (a read gets EOF, never hangs) and there
        # is no TTY (git won't page). The non-interactive env is passed in too, so editors/credential
        # prompts never block — the same anti-hang guarantee as the local sandbox.
        env_args = [arg for k, v in _NONINTERACTIVE_ENV.items() for arg in ("-e", f"{k}={v}")]
        # Named container so a timeout can actually STOP it: `docker run` timing out only kills the
        # client — the container stays detached on the daemon (still executing, holding the bind-mount,
        # exfiltrating) and `--rm` won't fire until it exits on its own. On timeout we `docker kill` it.
        import uuid

        name = f"chimera-{uuid.uuid4().hex[:12]}"
        argv = [
            "docker", "run", "--rm", "--name", name,
            "--network", "bridge" if self.network else "none",
            "--memory", self.memory,
            *(("--runtime", self.runtime) if self.runtime else ()),
            *env_args,
            "-v", f"{workdir}:/workspace",
            "-w", "/workspace",
            self.image, "sh", "-c", command,
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout + 15, stdin=subprocess.DEVNULL
            )
        except subprocess.TimeoutExpired:
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["docker", "kill", name], capture_output=True, timeout=15)  # noqa: S603,S607
            return SandboxResult(
                exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True
            )
        return SandboxResult(
            exit_code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or ""
        )
