"""The desktop bridge's discovery file, with nothing but the standard library.

`chimera mcp desktop` runs from `pip install chimera-agent[mcp]`, which does not bring FastAPI. It
used to import these helpers from `chimera.api.desktop_bridge`, whose module imports FastAPI at the
top, so the published 0.62.2 bridge failed on its first `list_tools` with "No module named
'fastapi'". The gate never saw it, because the gate installs every extra. Both the app, which
writes the file, and the MCP client, which reads it, import from here.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

BRIDGE_DIR_ENV = "CHIMERA_BRIDGE_DIR"
BRIDGE_FILE = "desktop-bridge.json"


# ---- the discovery file -------------------------------------------------------------------------


def bridge_path() -> Path:
    """Where the discovery file lives: ``$CHIMERA_BRIDGE_DIR`` or ``~/.chimera``."""
    override = os.environ.get(BRIDGE_DIR_ENV, "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".chimera"
    return base / BRIDGE_FILE


def write_discovery(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` so only the current user can read it, and atomically."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    # The mode is given to `open`, not applied afterwards: a chmod after the write would leave a
    # window where the token sits in a file anyone on the machine can read.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def read_discovery(path: Path | None = None) -> dict[str, Any] | None:
    """The discovery file's contents, or None when it is absent or unreadable."""
    target = path or bridge_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("url") or not data.get("token"):
        return None
    return data
