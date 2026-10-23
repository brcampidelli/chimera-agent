"""Docker sandbox — runs commands inside an ephemeral, isolated container.

The workspace is bind-mounted at ``/workspace`` (writable) so file edits persist; the
container root filesystem is discarded on exit (``--rm``), memory is capped, and the
network is **disabled by default**. This is real isolation for agent-run commands.

An optional OCI ``runtime`` (e.g. ``runsc`` for gVisor) can be set to harden the boundary:
gVisor interposes a userspace kernel that intercepts the container's syscalls, shrinking
the host-kernel attack surface a plain ``runc`` container leaves exposed — a drop-in step
below a full microVM. Default is empty (the daemon's default runtime, ``runc``).

When Docker is not available the command is refused with the reason, and nothing runs. It used to
degrade to a :class:`LocalSandbox` with a warning in the log: the person who chose a container got
their commands run on their own computer and saw nothing say so (plan M8, observed 2026-10-10). A
caller that wants the host in that case passes ``fallback=`` explicitly; choosing
``CHIMERA_SANDBOX=local`` is the setting for that.
"""

from __future__ import annotations

import subprocess
import time
from contextlib import suppress
from pathlib import Path

from chimera.proc.decode import console_text
from chimera.proc.stdio import no_window
from chimera.sandbox.base import Sandbox, SandboxResult
from chimera.sandbox.local import _NONINTERACTIVE_ENV
from chimera.telemetry import get_logger

_log = get_logger("sandbox.docker")

#: What a command gets when the docker sandbox was chosen and no daemon answers. Said to the model
#: and, through it, to the person: what happened, that nothing ran, and the two ways out.
DOCKER_UNAVAILABLE = (
    "error: the docker sandbox is selected (CHIMERA_SANDBOX=docker) but Docker is not reachable, "
    "so the command was refused and nothing ran on this computer. Start Docker and try again, or "
    "set CHIMERA_SANDBOX=local to run commands on this computer instead."
)
#: How long a "Docker is not reachable" answer is trusted before the daemon is asked again.
RECHECK_SECONDS = 30.0
#: 125 is what `docker run` itself exits with when the daemon cannot run the container.
DOCKER_UNAVAILABLE_EXIT = 125


def docker_refusal(sandbox: object) -> str | None:
    """:data:`DOCKER_UNAVAILABLE` when ``sandbox`` is a docker sandbox that would refuse right now.

    For the callers that ask the person before running on the host: a refused command never runs
    anywhere, so asking "run this on your computer?" first would be a question about something that
    is not going to happen. They check this before the question and return the refusal instead.
    """
    if isinstance(sandbox, DockerSandbox) and sandbox.fallback is None and not sandbox.available():
        return DOCKER_UNAVAILABLE
    return None


class DockerSandbox:
    def __init__(
        self,
        image: str = "python:3.12-slim",
        *,
        network: bool = False,
        memory: str = "512m",
        cpus: str = "2",
        pids_limit: int = 256,
        runtime: str | None = None,
        fallback: Sandbox | None = None,
    ) -> None:
        self.image = image
        self.network = network
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        # Optional OCI runtime (e.g. "runsc" for gVisor); empty/None uses the daemon default.
        self.runtime = (runtime or "").strip() or None
        # None unless a caller asked for one: without it, a missing daemon refuses rather than
        # running the command on the host.
        self.fallback: Sandbox | None = fallback
        self._available: bool | None = None
        self._checked_at = 0.0

    def is_isolated(self) -> bool:
        """Isolated only when Docker is actually reachable.

        Not True for a missing daemon even though nothing then runs: this answer feeds the host-exec
        gate and the posture screen, and "isolated" there must mean a container exists.
        """
        return self.available()

    def available(self) -> bool:
        """Whether the Docker CLI/daemon is reachable.

        A "yes" is kept for good. A "no" is asked again after :data:`RECHECK_SECONDS`: now that a
        missing daemon refuses commands instead of running them on the host, a long-lived app whose
        owner starts Docker after the first refusal must not keep refusing until it restarts.
        """
        stale = (
            self._available is False
            and self._checked_at > 0
            and time.monotonic() - self._checked_at > RECHECK_SECONDS
        )
        if self._available is None or stale:
            try:
                proc = subprocess.run(["docker", "version"], capture_output=True, timeout=10, **no_window())
                self._available = proc.returncode == 0
            except (FileNotFoundError, OSError, subprocess.SubprocessError):
                self._available = False
            self._checked_at = time.monotonic()
        return self._available

    def _argv(self, name: str, command: str, workdir: Path, env_args: list[str]) -> list[str]:
        """The full `docker run` command line.

        A method rather than an inline literal so the limits below can be asserted without a running
        daemon — which is the only way they get checked at all, since CI has no Docker and the
        machine this was written on had none either.
        """
        return [
            "docker", "run", "--rm", "--name", name,
            "--network", "bridge" if self.network else "none",
            "--memory", self.memory,
            # Pinned to `--memory` on purpose. Without it Docker grants swap equal to the memory
            # limit ON TOP of it, so a `--memory 512m` container can touch a gigabyte and the cap
            # reads as roughly decorative. Same value = no swap, which is what a limit should mean.
            "--memory-swap", self.memory,
            # A busy loop in a container with no CPU cap starves the machine the agent is running
            # on — including the agent.
            "--cpus", self.cpus,
            # The one flag here that is not copied from anyone: a fork bomb inside the container
            # exhausts the HOST's process table, because PIDs are not namespaced by default. The
            # existing `fork_bomb` lexical rule only fires under governance, which is off by default.
            "--pids-limit", str(self.pids_limit),
            # Deliberately NOT here: `--storage-opt size=`. Docker refuses it outside xfs with
            # pquota, and the common case (overlay2 on ext4, every default laptop install) would
            # turn a working sandbox into a hard failure. A disk cap that only works on one
            # filesystem is not a disk cap.
            *(("--runtime", self.runtime) if self.runtime else ()),
            *env_args,
            "-v", f"{workdir}:/workspace",
            "-w", "/workspace",
            self.image, "sh", "-c", command,
        ]

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        if not self.available():
            if self.fallback is not None:
                _log.warning("docker unavailable; running in the fallback sandbox the caller gave")
                return self.fallback.run(command, timeout=timeout, cwd=cwd)
            _log.warning("docker unavailable; command refused, nothing ran")
            return SandboxResult(exit_code=DOCKER_UNAVAILABLE_EXIT, stderr=DOCKER_UNAVAILABLE)

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
        argv = self._argv(name, command, workdir, env_args)
        try:
            proc = subprocess.run(
                # Named: the container writes UTF-8, but the `docker` CLI on the host writes its
                # own errors in the console's page, and `text=True` reads with the ANSI one.
                argv,
                capture_output=True,
                timeout=timeout + 15,
                stdin=subprocess.DEVNULL,
             **no_window())
        except subprocess.TimeoutExpired:
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["docker", "kill", name], capture_output=True, timeout=15, **no_window())  # noqa: S603,S607
            return SandboxResult(
                exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True
            )
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=console_text(proc.stdout),
            stderr=console_text(proc.stderr),
        )
