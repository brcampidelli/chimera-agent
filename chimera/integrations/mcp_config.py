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
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from chimera.integrations.mcp_pins import forget_pin
from chimera.telemetry import get_logger

_log = get_logger("integrations.mcp_config")


class McpServerConfig(BaseModel):
    """One MCP server over stdio or streamable HTTP. ``env`` may carry secrets."""

    name: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    token_env: str | None = None
    oauth_authorization_url: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_redirect_uri: str | None = None
    oauth_scope: str | None = None

    @model_validator(mode="after")
    def require_transport(self) -> McpServerConfig:
        if not self.url and not self.command:
            raise ValueError("MCP server requires either command or url")
        return self

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Keep historical stdio JSON byte-compatible; write only configured HTTP options."""
        result = super().model_dump(**kwargs)
        if self.url is None:
            return {key: result[key] for key in ("name", "command", "args", "env")}
        return {key: value for key, value in result.items() if value is not None}


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
    """Add ``cfg`` to the store, REPLACING any existing server of the same name. Returns the new list.

    Forgets the remembered Test of that name (see :func:`forget_test`): a replacement may carry a new
    token under the same key names, and the old token's result would otherwise be shown beside it.
    Forgets its approved tool manifest too (:func:`chimera.integrations.mcp_pins.forget_pin`): the
    owner is configuring a server, and its next mount is first sight again.
    """
    servers = [s for s in load_servers(path) if s.name != cfg.name]
    servers.append(cfg)
    save_servers(path, servers)
    forget_test(path, cfg.name)
    forget_pin(path, cfg.name)
    return servers


def remove_server(path: Path, name: str) -> bool:
    """Remove the server named ``name``. Returns True if one was removed, False if none matched.

    Forgets the remembered Test of that name either way, so a server added later under the same
    name does not inherit a stranger's result.
    """
    servers = load_servers(path)
    kept = [s for s in servers if s.name != name]
    forget_test(path, name)
    forget_pin(path, name)
    if len(kept) == len(servers):
        return False
    save_servers(path, kept)
    return True


# --- the remembered Test (mcp_tests.json) ------------------------------------------------------------
#
# The desktop app remembers the last Test of each server beside the store (see chimera.api.mcp_api,
# which writes it). FORGETTING lives here, in the store, and not in the app's routes: when it lived
# in the routes, `chimera mcp add sentry ... --env SENTRY_ACCESS_TOKEN=new` - a supported path, not a
# hand edit - replaced the token and kept "last tested ok, 7 tools" beside it, because the record's
# fingerprint leaves env VALUES out on purpose and only the app's add knew to drop it. Every writer
# of mcp.json goes through add_server/remove_server, so that is where the record has to go.

#: Beside ``mcp.json``, not inside it: the VPS and the CLI read ``mcp.json`` too, and a field they do
#: not know about is a field one of them eventually drops or chokes on.
TESTS_FILE = "mcp_tests.json"

#: Every read-modify-write of the remembered tests goes through this. The app's test endpoint runs on
#: a thread pool, so two Tests (or a Test and an Add) can interleave; without it each loads the file,
#: changes its own record and saves, and the second save erases the first.
TESTS_LOCK = threading.Lock()


def tests_path_for(mcp_path: Path) -> Path:
    """Where the remembered tests of the store at ``mcp_path`` live."""
    return Path(mcp_path).parent / TESTS_FILE


def load_test_records(path: Path) -> dict[str, dict[str, Any]]:
    """The remembered tests at ``path``; missing or unreadable is ``{}``.

    A memory of a test is a convenience; it must never be the reason the server list fails to load.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_test_records(path: Path, tests: dict[str, dict[str, Any]]) -> None:
    """Write ``tests`` to ``path`` atomically, through a temporary file of its OWN.

    With one shared ``.tmp`` name, a second writer (the CLI, a second copy of the app, another
    thread) truncates the first one's half-written file, and whichever renames second finds nothing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(tests, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def forget_test(mcp_path: Path, name: str) -> None:
    """Drop the remembered Test of ``name`` from beside the store at ``mcp_path``.

    Never raises for a disk that cannot be written: the server was already added or removed, and a
    memory that could not be cleared must not turn that into a failure.
    """
    path = tests_path_for(mcp_path)
    with TESTS_LOCK:
        tests = load_test_records(path)
        if name not in tests:
            return
        del tests[name]
        try:
            save_test_records(path, tests)
        except OSError as exc:
            _log.warning("could not forget the MCP test for %r: %s", name, type(exc).__name__)


# --- live connect helpers (the only subprocess-spawning code in this module) -----------------------


def probe_tools(cfg: McpServerConfig, *, connect_timeout: float = 10.0) -> list[dict[str, Any]]:
    """Live-connect ``cfg`` over its configured transport, list tools, then close the session.

    Returns ``[{"name", "description", "input_schema"}, ...]``. The schema comes along so the cues
    can be read over the parameter descriptions too: at first sight a pin is taken on trust, and the
    Test is the only review a server hostile from day one ever gets. Raises on any connect/handshake failure — the caller
    (CLI ``mcp test`` / the API test endpoint) is responsible for turning that into a short, secret-free
    error. This is the honest "is it reachable + what does it expose" probe: a tool list can only be
    produced by a REAL connect, so it is the only thing that proves a server is live.
    """
    from chimera.integrations import MCPConnector

    # Interactive: Test is the one place a person is present to complete an OAuth sign-in.
    session = _session_for(cfg, connect_timeout, interactive=True)
    # start() INSIDE the try. A start that times out has already spawned the server, and the
    # process is still there waiting on its handshake; with start() outside, the finally never ran
    # and every Test that timed out left one behind for the life of the app — for a bridge that
    # signs in through the browser, holding a sign-in nobody would ever use. start() now closes a
    # failed start itself (every caller needed it, and only this one had it); the finally stays for
    # a start that succeeds and a tool listing that then fails. A second close is harmless.
    try:
        session.start()
        connector = MCPConnector(cfg.name, session)
        return [
            {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
            for tool in connector.tools()
        ]
    finally:
        session.close()  # best-effort teardown so a probe never leaks a live server


def autoload_into_registry(
    registry: Any,
    servers: list[McpServerConfig],
    *,
    connect_timeout: float = 10.0,
    mcp_path: Path | None = None,
) -> int:
    """Connect every server in ``servers`` and pour its tools into ``registry``. Returns the tool count.

    Each server is connected with a PER-SERVER timeout and skipped GRACEFULLY on any failure (a broken
    server logs a warning and is skipped — it must never break agent boot). The connected sessions are
    left OPEN on purpose: the registered tools call back into them at run time. Names are namespaced
    ``<server>_<tool>`` so a remote server can't shadow a builtin (see ConnectorRegistry).

    With ``mcp_path`` — the store the servers came from — each server goes through the same manifest
    gate as the shared pool (:func:`chimera.integrations.mcp_pool.pinned_session`): one whose tools
    changed since they were approved is held. Without it there is no pin file to compare against;
    no shipped surface calls this function (they all mount through the pool), so the parameter is
    for a caller that holds a store, and the pool is the place the gate is enforced.
    """
    from chimera.integrations import ConnectorRegistry, MCPConnector
    from chimera.integrations.mcp_pool import pinned_session

    connectors = ConnectorRegistry()
    for cfg in servers:
        try:
            session = _session_for(cfg, connect_timeout).start()
            mounted: Any = session
            if mcp_path is not None:
                mounted = pinned_session(mcp_path, cfg.name, session)
                if mounted is None:
                    continue
            connectors.register(MCPConnector(cfg.name, mounted, name_prefix=f"{cfg.name}_"))
        except Exception as exc:  # noqa: BLE001 — a broken server must never break agent boot
            _log.warning("MCP autoload: skipping server %r (%s)", cfg.name, type(exc).__name__)
    return connectors.into_tool_registry(registry)


def _session_for(cfg: McpServerConfig, connect_timeout: float, *, interactive: bool = False) -> Any:
    """Select the configured transport without changing legacy stdio behavior.

    ``interactive`` reaches only the HTTP transport: whether an OAuth sign-in may open a browser.
    """
    if cfg.url:
        from chimera.integrations import StreamableHTTPMCPSession

        return StreamableHTTPMCPSession.from_config(
            cfg, connect_timeout=connect_timeout, interactive=interactive
        )
    from chimera.integrations import StdioMCPSession

    return StdioMCPSession(cfg.command, cfg.args or None, cfg.env or None, connect_timeout=connect_timeout)
