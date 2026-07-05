"""Execution sandboxes: pluggable backends for running shell commands.

* :class:`LocalSandbox` — runs on the host (timeout + working dir); the default.
* :class:`DockerSandbox` — runs in an ephemeral, network-isolated container, with a
  graceful fallback to local when Docker is absent.

Select the backend with ``CHIMERA_SANDBOX=local|docker`` (image via
``CHIMERA_SANDBOX_IMAGE``, hardened OCI runtime via ``CHIMERA_SANDBOX_RUNTIME=runsc``
for gVisor); :func:`get_sandbox` reads the settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.sandbox.base import Sandbox, SandboxResult
from chimera.sandbox.docker import DockerSandbox
from chimera.sandbox.local import LocalSandbox

if TYPE_CHECKING:
    from chimera.config import Settings


def get_sandbox(settings: Settings | None = None) -> Sandbox:
    """Return the configured sandbox backend (local by default)."""
    from chimera.config import get_settings

    settings = settings or get_settings()
    if (settings.sandbox or "local").lower() == "docker":
        return DockerSandbox(image=settings.sandbox_image, runtime=settings.sandbox_runtime)
    return LocalSandbox()


__all__ = ["Sandbox", "SandboxResult", "LocalSandbox", "DockerSandbox", "get_sandbox"]
