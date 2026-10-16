"""Per-server MCP timeouts persist without changing legacy config serialization."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

from chimera.integrations.mcp_client import StdioMCPSession
from chimera.integrations.mcp_config import (
    McpServerConfig,
    _session_for,
    load_servers,
    save_servers,
)


def test_call_timeout_serializes_only_when_configured(tmp_path: Any) -> None:
    configured = McpServerConfig(name="x", command="c", call_timeout=600)
    assert configured.model_dump()["call_timeout"] == 600

    legacy = McpServerConfig(name="legacy", command="c")
    assert "call_timeout" not in legacy.model_dump()
    assert "connect_timeout" not in legacy.model_dump()

    path = tmp_path / "mcp.json"
    save_servers(path, [configured])
    loaded = load_servers(path)
    assert len(loaded) == 1
    assert loaded[0].call_timeout == 600


def test_catalog_timeout_is_separate_from_connect_timeout(monkeypatch: Any) -> None:
    configured = McpServerConfig(
        name="slow-catalog", command="server", connect_timeout=4, catalog_timeout=19
    )
    session = _session_for(configured, 30)

    assert session.connect_timeout == 4
    assert session.catalog_timeout == 19


def test_list_tools_uses_catalog_timeout_not_connect_or_call_timeout(monkeypatch: Any) -> None:
    session = StdioMCPSession(
        "unused", connect_timeout=4, call_timeout=7, catalog_timeout=19
    )
    session._loop = object()
    session._session = SimpleNamespace(list_tools=lambda: _empty_catalog())
    observed: list[float | None] = []

    class _Future:
        def result(self, timeout: float | None = None) -> Any:
            observed.append(timeout)
            return SimpleNamespace(tools=[])

    def submit(coroutine: Any, _loop: Any) -> _Future:
        coroutine.close()
        return _Future()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)
    assert session.list_tools() == []
    assert observed == [19]


def test_stdio_spawn_receives_configured_cwd(monkeypatch: Any) -> None:
    session = StdioMCPSession("server", cwd="/approved/workspace")
    seen: list[Any] = []

    class _Parameters:
        def __init__(self, **kwargs: Any) -> None:
            seen.append(kwargs)

    class _StdioContext:
        async def __aenter__(self) -> tuple[str, str]:
            return "reader", "writer"

        async def __aexit__(self, *_args: Any) -> None:
            return None

    class _ClientSession:
        async def __aenter__(self) -> _ClientSession:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def initialize(self) -> None:
            session._stop_event.set()

    def stdio_client(params: Any) -> _StdioContext:
        assert params.cwd == "/approved/workspace"
        return _StdioContext()

    mcp = ModuleType("mcp")
    mcp.ClientSession = _ClientSession
    mcp.StdioServerParameters = _Parameters
    client = ModuleType("mcp.client")
    stdio = ModuleType("mcp.client.stdio")
    stdio.stdio_client = stdio_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio)

    asyncio.run(session._serve())

    assert seen == [{"command": "server", "args": [], "env": None, "cwd": "/approved/workspace"}]


async def _empty_catalog() -> Any:
    return SimpleNamespace(tools=[])


def test_stdio_session_call_timeout_defaults_and_override() -> None:
    assert StdioMCPSession("unused", call_timeout=5).call_timeout == 5
    assert StdioMCPSession("unused").call_timeout == 120.0
