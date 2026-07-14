"""Read/write + live-test the configured MCP servers for the desktop app's MCP screen.

Honesty is the whole point of this module:

- **Config reads/writes are cheap file I/O — they NEVER connect.** ``list_servers``/``add``/``remove``
  only touch ``.chimera/mcp.json`` (via :mod:`chimera.integrations.mcp_config`). A server appearing in
  the list means "configured", never "connected".
- **``env`` VALUES are never returned.** ``list_servers`` reports only the env KEY names (``env_keys``);
  the secret values stay in the local store, never crossing the API.
- **``test`` is the ONLY connecting call**, and it is the ONLY thing that can prove a server is live: a
  real stdio connect + tool enumeration. Every failure is caught and flattened to a short, secret-free
  ``{ok:false, tools:[], error}`` — never a stack trace, never an env value, never a 500.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.integrations.mcp_config import (
    McpServerConfig,
    add_server,
    load_servers,
    probe_tools,
    remove_server,
)
from chimera.telemetry import get_logger

_log = get_logger("api.mcp")

# A test connect is bounded so a misbehaving server can't hang the request thread.
_TEST_CONNECT_TIMEOUT = 12.0


def _mcp_path(home: Path) -> Path:
    return Path(home) / "mcp.json"


def list_servers(home: Path) -> dict[str, Any]:
    """The configured servers as ``{servers:[{name, command, args, env_keys}], count}``. No connect.

    ``env_keys`` is the SORTED list of env variable NAMES only — the values are never returned.
    """
    servers = load_servers(_mcp_path(home))
    out = [
        {
            "name": s.name,
            "command": s.command,
            "args": list(s.args),
            "env_keys": sorted(s.env),
        }
        for s in servers
    ]
    return {"servers": out, "count": len(out)}


def add(home: Path, name: str, command: str, args: list[str], env: dict[str, str]) -> dict[str, Any]:
    """Add (or replace-by-name) a server, then return the refreshed list (env values still masked)."""
    cfg = McpServerConfig(name=name, command=command, args=list(args), env=dict(env))
    add_server(_mcp_path(home), cfg)
    return list_servers(home)


def remove(home: Path, name: str) -> bool:
    """Remove a server by name. Returns True if one was removed."""
    return remove_server(_mcp_path(home), name)


def _live_test(cfg: McpServerConfig) -> list[dict[str, str]]:
    """Connect ``cfg`` and return its tools as ``[{name, description}]``. Isolated so tests can
    monkeypatch it (``chimera.api.mcp_api._live_test``) without spawning a real subprocess."""
    return probe_tools(cfg, connect_timeout=_TEST_CONNECT_TIMEOUT)


def test_server(home: Path, name: str) -> dict[str, Any]:
    """Live-connect the named server and report its tools, or a short secret-free error. Never raises.

    ``{ok:true, tools:[{name, description}], error:null}`` on a real connect; ``{ok:false, tools:[],
    error}`` on ANY failure (unknown server, connect timeout, missing ``mcp`` extra, handshake error).
    The error string is a short class-name-based summary — it never carries an env value or a traceback.
    """
    servers = load_servers(_mcp_path(home))
    cfg = next((s for s in servers if s.name == name), None)
    if cfg is None:
        return {"ok": False, "tools": [], "error": "no such server"}
    try:
        tools = _live_test(cfg)
        return {"ok": True, "tools": tools, "error": None}
    except Exception as exc:  # noqa: BLE001 — every failure becomes a short, secret-free error
        _log.warning("MCP test for %r failed: %s", name, type(exc).__name__)
        return {"ok": False, "tools": [], "error": _short_error(exc)}


def _short_error(exc: Exception) -> str:
    """A short, secret-free failure message: the exception's own text if it's brief and clean, else its
    class name. Guards against an env value or a long traceback-like string leaking into the UI."""
    text = str(exc).strip()
    if text and len(text) <= 200 and "\n" not in text:
        return text
    return type(exc).__name__
