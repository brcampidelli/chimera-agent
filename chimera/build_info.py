"""Build identity carried by durable run receipts."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path

from chimera import __version__

CHIMERA_VERSION = __version__


def _checkout_sha(package_dir: Path) -> str:
    """HEAD of the Chimera source checkout this package was imported from, or "".

    Only the package's OWN repository counts: its parent directory must hold both ``.git`` and a
    ``pyproject.toml`` naming ``chimera-agent``. Walking every ancestor for any ``.git`` (as this
    first did) recorded the SHA of whatever project a user's virtualenv happened to live in — a
    plausible, wrong identity on every receipt, and nothing would ever flag it.
    """
    try:
        root = package_dir.resolve().parent
        if not (root / ".git").exists():
            return ""
        pyproject = root / "pyproject.toml"
        if 'name = "chimera-agent"' not in pyproject.read_text(encoding="utf-8"):
            return ""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
            # The desktop sidecar is a windowless process; without this every lookup flashes a
            # console on Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, RuntimeError, UnicodeDecodeError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


@lru_cache(maxsize=1)
def chimera_git_sha() -> str:
    """The running code's git SHA: wheel metadata when present, else the source checkout's HEAD.

    Resolved on first use and cached for the process — never at import, so importing the fusion or
    runs modules does not spawn ``git``, and receipts never depend on a later workspace's state.
    """
    try:
        values = metadata("chimera-agent").get_all("Chimera-Git-SHA", []) or []
        wheel_sha = str(values[-1]).strip() if values else ""
    except PackageNotFoundError:
        wheel_sha = ""
    except Exception:  # noqa: BLE001 — build identity must never make the application fail
        wheel_sha = ""
    return wheel_sha or _checkout_sha(Path(__file__).parent)
