"""Execution sandboxes: pluggable backends for running shell commands.

* :class:`~chimera.sandbox.os_sandbox.OsSandbox` — the platform's kernel sandbox (Seatbelt on
  macOS, bubblewrap on Linux), network off, writes confined to the working directory. **The
  default**, via ``auto``.
* :class:`LocalSandbox` — runs on the host (timeout + working dir). What ``auto`` falls back to
  where no kernel sandbox is available, and what ``local`` selects deliberately.
* :class:`DockerSandbox` — runs in an ephemeral, network-isolated container, with a
  graceful fallback to local when Docker is absent.

Select the backend with ``CHIMERA_SANDBOX=auto|os|local|docker`` (image via
``CHIMERA_SANDBOX_IMAGE``, hardened OCI runtime via ``CHIMERA_SANDBOX_RUNTIME=runsc``
for gVisor); :func:`get_sandbox` reads the settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.sandbox.base import Sandbox, SandboxResult
from chimera.sandbox.docker import DockerSandbox
from chimera.sandbox.local import LocalSandbox
from chimera.sandbox.os_sandbox import OsSandbox, os_sandbox_available, unavailable_reason
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings

_log = get_logger("sandbox")
_warned = False


def _warn_unsandboxed(reason: str) -> None:
    """Say it once per process. Repeated per command it becomes noise nobody reads; said once it is
    the difference between a user who knows the boundary is absent and one who assumes it is there."""
    global _warned
    if not _warned:
        _warned = True
        _log.warning("commands run WITHOUT an OS sandbox: %s", reason)


def get_sandbox(settings: Settings | None = None) -> Sandbox:
    """Return the configured sandbox backend.

    The default is ``auto``: use the platform's kernel sandbox where there is one, and say out loud
    when there is not. It used to be ``local`` — the host, with a timeout and a working directory —
    which meant the shipped default had no boundary at all and the only thing in front of a command
    was a confirmation prompt.

    ``auto`` resolves to :class:`LocalSandbox` on a machine with no usable sandbox rather than to an
    :class:`OsSandbox` that would fall through to the same place. Both run the command identically;
    the difference is that this way the warning is emitted once, here, instead of once per command,
    and ``is_isolated()`` is False for the plain structural reason that the object cannot isolate.
    """
    from chimera.config import get_settings

    settings = settings or get_settings()
    choice = (settings.sandbox or "auto").lower()
    if choice in {"auto", "os"}:
        from chimera.sandbox.os_sandbox import OsSandbox, os_sandbox_available, unavailable_reason

        if os_sandbox_available():
            return OsSandbox()
        _warn_unsandboxed(unavailable_reason())
        return LocalSandbox()
    if choice == "docker":
        # Every one of these was a constructor parameter the factory never passed. `network` and
        # `memory` in particular were accepted, documented, and dead: no caller could reach them, so
        # the container was hard-wired to no-network/512m whatever the settings said.
        return DockerSandbox(
            image=settings.sandbox_image,
            network=(settings.sandbox_network or "none").lower() == "bridge",
            memory=settings.sandbox_memory,
            cpus=settings.sandbox_cpus,
            pids_limit=settings.sandbox_pids_limit,
            runtime=settings.sandbox_runtime,
        )
    return LocalSandbox()


__all__ = [
    "Sandbox",
    "SandboxResult",
    "LocalSandbox",
    "DockerSandbox",
    "OsSandbox",
    "get_sandbox",
    "os_sandbox_available",
    "unavailable_reason",
]
