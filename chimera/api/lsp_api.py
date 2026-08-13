"""One `ruff server` per workspace, kept warm.

Starting a language server costs a process spawn and an `initialize` round trip — tens of
milliseconds, which is nothing once and everything on every keystroke. So the server is held per
workspace and reused, which is also what makes its diagnostics incremental in the only sense that
matters: the second file benefits from a process that already read the project's configuration.

The pool is bounded and evicted for the same reasons the ACP registry is, and shares the same
exit-time reaper — a language server outliving the app is a process holding a folder open.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from chimera.lsp.client import RuffClient
from chimera.proc.stdio import resolve_program
from chimera.telemetry import get_logger
from chimera.tools.workspace import PathEscapesWorkspaceError, resolve_in_workspace

_log = get_logger("api.lsp")

#: Language servers alive at once. One per project someone has open; more than a handful means
#: something is opening workspaces nobody is looking at.
MAX_SERVERS = 4

#: Seconds a server may sit unused before it is closed.
IDLE_SECONDS = 900.0

#: How long to wait for the server to publish after handing over a buffer. Ruff answers a single
#: file in milliseconds; this is the ceiling before reporting what we have rather than blocking a
#: request. Hitting it means reporting the PREVIOUS result, which is the one honest option left:
#: an empty list would claim a clean file nobody finished checking.
SETTLE_SECONDS = 2.0

_servers: dict[str, tuple[RuffClient, float]] = {}
_lock = threading.Lock()


def ruff_available() -> bool:
    """Whether `ruff` resolves on this machine. Asked before a spawn, so the answer is a sentence
    rather than a stack trace about a missing program."""
    resolved = resolve_program("ruff")
    return resolved != "ruff" or Path(resolved).exists()


def _evict_idle() -> None:
    now = time.monotonic()
    with _lock:
        stale = [key for key, (_, used) in _servers.items() if now - used > IDLE_SECONDS]
        dead = [key for key, (client, _) in _servers.items() if not client.is_alive()]
    for key in {*stale, *dead}:
        close_server(key)


def _server_for(workspace: Path) -> RuffClient:
    key = str(workspace)
    _evict_idle()
    with _lock:
        found = _servers.get(key)
        if found is not None and found[0].is_alive():
            _servers[key] = (found[0], time.monotonic())
            return found[0]

    client = RuffClient(workspace).start()  # outside the lock: a spawn takes time
    with _lock:
        existing = _servers.get(key)
        if existing is not None and existing[0].is_alive():
            client.close()  # two requests raced; keep the one already installed
            return existing[0]
        _servers[key] = (client, time.monotonic())
        over = len(_servers) - MAX_SERVERS
        oldest = (
            [k for k, _ in sorted(_servers.items(), key=lambda kv: kv[1][1])[:over]]
            if over > 0
            else []
        )
    for key_to_close in oldest:
        close_server(key_to_close)
    return client


def close_server(key: str) -> None:
    with _lock:
        found = _servers.pop(key, None)
    if found is not None:
        found[0].close()


def close_all() -> None:
    with _lock:
        clients = [client for client, _ in _servers.values()]
        _servers.clear()
    for client in clients:
        client.close()


def diagnostics_for_buffer(workspace: Path, path: str, text: str) -> dict[str, Any]:
    """Diagnose one buffer. Never raises; a failure is an empty list plus a reason.

    ``available: false`` is not the same as an empty list of problems, and conflating them would
    show a clean editor on a machine where nothing ever looked.
    """
    if not ruff_available():
        return {
            "diagnostics": [],
            "available": False,
            "note": "ruff is not installed here (pip install ruff, or the 'dev' extra)",
        }
    try:
        target = resolve_in_workspace(workspace, path)
    except PathEscapesWorkspaceError:
        return {"diagnostics": [], "available": False, "note": "that path is outside the workspace"}
    if target.suffix.lower() not in (".py", ".pyi"):
        # Said rather than answered with an empty list: ruff has no opinion about a .ts file, and a
        # silent empty answer there reads as "this file is fine".
        return {"diagnostics": [], "available": False, "note": "ruff only reads Python"}

    try:
        client = _server_for(Path(workspace))
    except Exception as exc:  # noqa: BLE001 — a dead server is a degraded editor, not a 500
        _log.warning("could not start ruff server: %s", exc)
        return {"diagnostics": [], "available": False, "note": "the language server did not start"}

    try:
        # The count is read BEFORE the text goes out, so a server that answers between the two
        # cannot leave us waiting for a result already delivered.
        before = client.publish_count(target)
        found = (
            client.wait_for_publish(target, before, SETTLE_SECONDS)
            if client.sync_document(target, text)
            else client.diagnostics_for(target)  # identical buffer: nothing to recompute
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("diagnostics failed: %s", exc)
        return {"diagnostics": [], "available": False, "note": "the language server stopped"}

    return {
        "diagnostics": [
            {
                "path": d.path,
                "line": d.line,
                "column": d.column,
                "end_line": d.end_line,
                "end_column": d.end_column,
                "severity": d.severity,
                "code": d.code,
                "message": d.message,
            }
            for d in found
        ],
        "available": True,
        "note": "",
    }
