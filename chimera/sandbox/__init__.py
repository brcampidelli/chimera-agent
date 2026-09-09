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
from chimera.sandbox.os_sandbox import (
    OsSandbox,
    os_sandbox_available,
    unavailable_cause,
    unavailable_reason,
)
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings

_log = get_logger("sandbox")
_warned = False


def claim_unsandboxed_notice() -> str:
    """Take responsibility for telling this user there is no OS sandbox. Returns the reason, once.

    ``""`` when a sandbox IS available, and ``""`` on every call after the first in a process — so
    the fact is stated once, by whichever caller can state it best. An interactive surface claims it
    before building its tools and prints one line in its own banner; everything else lets
    :func:`get_sandbox` log it as before.

    The reason this is a claim rather than a log call: the sentence is four lines long and was
    printed as a WARNING block above the ``chat`` banner at every single start, on a machine where
    the answer can never change (Windows has no OS sandbox at all). A warning that appears every
    time and never changes is one people learn to scroll past, which costs exactly the readers it
    was written for.
    """
    global _warned
    if _warned:
        return ""
    from chimera.sandbox.os_sandbox import os_sandbox_available, unavailable_reason

    if os_sandbox_available():
        return ""
    _warned = True
    return unavailable_reason()


def _warn_unsandboxed() -> None:
    """Log the notice, if this process has not already delivered it some other way."""
    reason = claim_unsandboxed_notice()
    if reason:
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
        from chimera.sandbox.os_sandbox import OsSandbox, os_sandbox_available

        if os_sandbox_available():
            return OsSandbox()
        _warn_unsandboxed()
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
    "claim_unsandboxed_notice",
    "get_sandbox",
    "os_sandbox_available",
    "unavailable_cause",
    "unavailable_reason",
]
