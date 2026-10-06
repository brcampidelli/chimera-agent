"""Build identity carried by durable run receipts."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path

from chimera import __version__


def _checkout_sha(package_dir: Path) -> str:
    """Read HEAD only when this package's source directory belongs to a checkout."""
    try:
        root = package_dir.resolve()
        if not any((parent / ".git").exists() for parent in (root, *root.parents)):
            return ""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


@lru_cache(maxsize=1)
def _git_sha() -> str:
    """Use wheel metadata when present; otherwise use git only for a source checkout."""
    try:
        wheel_sha = str(metadata("chimera-agent").get_all("Chimera-Git-SHA", [])[-1]).strip()
    except (PackageNotFoundError, OSError, ValueError):
        wheel_sha = ""
    except Exception:  # noqa: BLE001 — build identity must never make the application fail
        wheel_sha = ""
    return wheel_sha or _checkout_sha(Path(__file__).parent)


# Resolved once per process. Receipts must never depend on the state of a later workspace.
CHIMERA_VERSION = __version__
CHIMERA_GIT_SHA = _git_sha()
