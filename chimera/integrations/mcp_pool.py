"""The configured MCP servers, connected once per process and shared by every surface.

The chat path has had MCP tools since autoload existed: ``chimera app`` connects the servers at
boot and hands the connectors to the session factory. Every other surface — the Code screen, a run,
both orchestration routes — builds its registry through
:func:`~chimera.api.code_api.assemble_registry`, which starts from ``default_registry`` and never
saw them. So a user could connect GitHub, watch the Test button prove it live, and find the coding
agent had no idea it existed.

**Why this is a pool and not a connect-per-call.** ``assemble_registry`` runs once per TURN, and per
worker inside a fan-out. Connecting there without reuse would spawn a Docker container per message
and tear it down again — the GitHub server alone would re-run its OAuth handshake every time. The
sessions are long-lived by design (``autoload_into_registry`` documents the same thing), so they
belong to the process, not to the request.

**Connected once, never reconnected.** Editing ``mcp.json`` does not take effect until restart, which
is exactly what the MCP screen already tells the user about the autoload toggle. Reconnecting on a
config change sounds friendlier and would leak the previous sessions — a subprocess per edit, held
open by a registry nobody points at any more.

**Off unless asked.** With ``mcp_autoload`` off — the default — nothing here spawns anything, and a
stock install behaves exactly as it did.
"""

from __future__ import annotations

import threading
from typing import Any

from chimera.config import Settings
from chimera.telemetry import get_logger

_log = get_logger("integrations.mcp_pool")

#: Same bound the CLI uses. A server that cannot answer in ten seconds is skipped rather than
#: waited on — the cost lands on the FIRST turn after boot, and a turn that hangs on somebody's
#: broken config is worse than a turn without their tools.
_CONNECT_TIMEOUT = 10.0

_lock = threading.Lock()
_pool: Any = None
_tried = False


def connectors(settings: Settings) -> Any:
    """The connected MCP servers, or ``None`` when autoload is off or nothing is configured.

    Built under a lock because several turns can arrive at once and each would otherwise start its
    own set of subprocesses — the failure this pool exists to prevent, reached by the code that
    prevents it.
    """
    global _pool, _tried
    if not settings.mcp_autoload:
        return None
    with _lock:
        if not _tried:
            _tried = True
            _pool = _build(settings)
    return _pool


def _build(settings: Settings) -> Any:
    from chimera.integrations import ConnectorRegistry, MCPConnector, StdioMCPSession
    from chimera.integrations.mcp_config import load_servers

    servers = load_servers(settings.home / "mcp.json")
    if not servers:
        return None
    pool = ConnectorRegistry()
    for cfg in servers:
        try:
            session = StdioMCPSession(
                cfg.command, cfg.args or None, cfg.env or None, connect_timeout=_CONNECT_TIMEOUT
            ).start()
            # Namespaced, so a server cannot publish a tool called `read_file` and shadow the one
            # that respects the write region. `into_tool_registry` skips collisions anyway, but a
            # prefix means there is nothing to collide over.
            pool.register(MCPConnector(cfg.name, session, name_prefix=f"{cfg.name}_"))
        except Exception as exc:  # noqa: BLE001 — a broken server must never break a turn
            _log.warning("MCP: skipping server %r (%s)", cfg.name, type(exc).__name__)
    return pool if pool.names() else None


def reset_for_tests() -> None:
    """Forget the pool so a test can build a different one.

    Named for what it is. Process-wide state that tests can only set and never clear is state that
    makes the second test in a file depend on the first.
    """
    global _pool, _tried
    with _lock:
        _pool = None
        _tried = False
