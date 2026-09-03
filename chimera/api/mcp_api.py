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

    ``ok`` answers "does this server work". ``reaches_agent`` answers the question the person
    clicking Test is actually asking — "can the agent use it" — and the two are not the same. See
    :func:`_reach`.
    """
    servers = load_servers(_mcp_path(home))
    cfg = next((s for s in servers if s.name == name), None)
    if cfg is None:
        return {"ok": False, "tools": [], "error": "no such server", **_reach(name)}
    try:
        tools = _live_test(cfg)
        return {"ok": True, "tools": tools, "error": None, **_reach(name)}
    except Exception as exc:  # noqa: BLE001 — every failure becomes a short, secret-free error
        _log.warning("MCP test for %r failed: %s", name, type(exc).__name__)
        return {"ok": False, "tools": [], "error": _short_error(exc), **_reach(name)}


def _reach(name: str) -> dict[str, Any]:
    """Whether a run started right now would receive this server's tools, and if not, why not.

    A live connect proves the server works. It does not prove the *agent* can reach it, and the gap
    between those two facts was measured: a server was registered, Test answered "ok, 4 tools" and
    listed all four, and the next run made twenty-two tool calls over nineteen minutes without one
    of them being from the server — because ``mcp_autoload`` is off by default. Nothing on the
    screen said so. "It works" and "you can use it" looked identical, and only one was true.

    Three states, and each has a different remedy:

    * autoload off — nothing reaches any run. Turn it on; no relaunch needed, because the pool is
      built lazily on the first turn that asks for it.
    * autoload on, pool not built yet — the next run builds it and picks this server up.
    * autoload on, pool already built without this name — this server was added after the servers
      were connected, and the pool is deliberately never rebuilt in a process (see
      :mod:`chimera.integrations.mcp_pool`). It needs a relaunch.

    The reason is an ENUM, not a sentence. The app is translated into ten languages, and a screen
    that prints an English string from the server is a screen where one line is in the wrong
    language — the same rule the orchestration frames already follow for ``fell_back.reason``.

    Reads the pool WITHOUT building it, so asking the question never has the side effect of
    answering it — clicking Test must not spawn a subprocess per configured server.
    """
    from chimera.config import get_settings
    from chimera.integrations.mcp_pool import pool_state

    try:
        estado = pool_state(get_settings())
    except Exception as exc:  # noqa: BLE001 -- a status read must never fail a test
        _log.debug("MCP reach unknown: %s", type(exc).__name__)
        return {"reaches_agent": None, "reaches_agent_reason": None}

    if not estado.autoload:
        return {"reaches_agent": False, "reaches_agent_reason": "autoload_off"}
    if estado.connected is None or name in estado.connected:
        return {"reaches_agent": True, "reaches_agent_reason": None}
    return {"reaches_agent": False, "reaches_agent_reason": "added_after_connect"}


def _short_error(exc: Exception) -> str:
    """A short, secret-free failure message: the exception's own text if it's brief and clean, else its
    class name. Guards against an env value or a long traceback-like string leaking into the UI."""
    text = str(exc).strip()
    if text and len(text) <= 200 and "\n" not in text:
        return text
    return type(exc).__name__
