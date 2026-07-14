"""Persisted MCP server configuration — the source of truth for configured MCP servers.

Today nothing persists which MCP servers exist; only ``examples/mcp_github.py`` wires one by hand.
This module is the durable store the CLI (``chimera mcp add/list/remove/test``) and the desktop app
both read/write, so an MCP server becomes a real, configurable capability instead of a code change.

The store is a single ``mcp.json`` document (canonical path ``settings.home / "mcp.json"``), written
byte-stably (sorted keys, indent 2, trailing newline) and atomically — mirroring the ``.chimera``
JSON-store convention. A malformed entry is skipped on load, never crashes, and a missing file loads
as ``[]``. Secrets in ``env`` live in this local file (like ``.env``); they are never LOGGED.

The two live-connect helpers here — :func:`probe_tools` (connect, list, close) and
:func:`autoload_into_registry` (connect, keep open, register) — are the ONLY code paths that spawn a
subprocess and speak the async MCP handshake. Everything else in this module is pure file I/O.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from chimera.telemetry import get_logger

_log = get_logger("integrations.mcp_config")


class McpServerConfig(BaseModel):
    """One configured MCP server: how to launch it over stdio. ``env`` may carry secrets."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


def load_servers(path: Path) -> list[McpServerConfig]:
    """Load configured servers from ``path``; missing file -> ``[]``, malformed entries skipped."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:  # pragma: no cover - defensive: a truncated/corrupt file must not crash
        _log.warning("skipping unreadable mcp.json")
        return []
    if not isinstance(raw, list):
        return []
    out: list[McpServerConfig] = []
    for entry in raw:
        try:
            out.append(McpServerConfig.model_validate(entry))
        except ValueError:  # a single malformed entry is skipped, the rest still load
            _log.warning("skipping malformed mcp server entry")
    return out


def save_servers(path: Path, servers: list[McpServerConfig]) -> None:
    """Persist ``servers`` to ``path`` byte-stably (sorted keys, indent 2, trailing newline), atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [s.model_dump() for s in servers]
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # atomic: a crash mid-write must not truncate the store


def add_server(path: Path, cfg: McpServerConfig) -> list[McpServerConfig]:
    """Add ``cfg`` to the store, REPLACING any existing server of the same name. Returns the new list."""
    servers = [s for s in load_servers(path) if s.name != cfg.name]
    servers.append(cfg)
    save_servers(path, servers)
    return servers


def remove_server(path: Path, name: str) -> bool:
    """Remove the server named ``name``. Returns True if one was removed, False if none matched."""
    servers = load_servers(path)
    kept = [s for s in servers if s.name != name]
    if len(kept) == len(servers):
        return False
    save_servers(path, kept)
    return True


# --- live connect helpers (the only subprocess-spawning code in this module) -----------------------


def probe_tools(cfg: McpServerConfig, *, connect_timeout: float = 10.0) -> list[dict[str, str]]:
    """Live-connect ``cfg`` over stdio, list its tools, then CLOSE the session (leaves no subprocess).

    Returns ``[{"name", "description"}, ...]``. Raises on any connect/handshake failure — the caller
    (CLI ``mcp test`` / the API test endpoint) is responsible for turning that into a short, secret-free
    error. This is the honest "is it reachable + what does it expose" probe: a tool list can only be
    produced by a REAL connect, so it is the only thing that proves a server is live.
    """
    from chimera.integrations import MCPConnector, StdioMCPSession

    session = StdioMCPSession(
        cfg.command, cfg.args or None, cfg.env or None, connect_timeout=connect_timeout
    ).start()
    try:
        connector = MCPConnector(cfg.name, session)
        return [{"name": tool.name, "description": tool.description} for tool in connector.tools()]
    finally:
        session.close()  # best-effort teardown so a probe never leaks a live server


def autoload_into_registry(
    registry: Any, servers: list[McpServerConfig], *, connect_timeout: float = 10.0
) -> int:
    """Connect every server in ``servers`` and pour its tools into ``registry``. Returns the tool count.

    Each server is connected with a PER-SERVER timeout and skipped GRACEFULLY on any failure (a broken
    server logs a warning and is skipped — it must never break agent boot). The connected sessions are
    left OPEN on purpose: the registered tools call back into them at run time. Names are namespaced
    ``<server>_<tool>`` so a remote server can't shadow a builtin (see ConnectorRegistry).
    """
    from chimera.integrations import ConnectorRegistry, MCPConnector, StdioMCPSession

    connectors = ConnectorRegistry()
    for cfg in servers:
        try:
            session = StdioMCPSession(
                cfg.command, cfg.args or None, cfg.env or None, connect_timeout=connect_timeout
            ).start()
            connectors.register(MCPConnector(cfg.name, session, name_prefix=f"{cfg.name}_"))
        except Exception as exc:  # noqa: BLE001 — a broken server must never break agent boot
            _log.warning("MCP autoload: skipping server %r (%s)", cfg.name, type(exc).__name__)
    return connectors.into_tool_registry(registry)
