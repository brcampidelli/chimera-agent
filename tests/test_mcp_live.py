"""Live integration: spin up a REAL MCP server over stdio and drive it via Chimera.

Spawns ``mcp_demo_server.py`` (a FastMCP server) as a subprocess, connects with
Chimera's stdio MCP client (real ``initialize`` + ``tools/list`` + ``tools/call``
handshake), registers the server's tools into a ``ToolRegistry``, and calls them.

Marked ``integration`` (deselected by default); needs the ``mcp`` extra installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chimera.integrations import connect_stdio
from chimera.integrations.connectors import ConnectorRegistry
from chimera.tools.registry import ToolRegistry

pytestmark = pytest.mark.integration

SERVER = str(Path(__file__).parent / "mcp_demo_server.py")


def test_mcp_stdio_list_register_and_call() -> None:
    connector = connect_stdio("demo", sys.executable, [SERVER])
    try:
        tools = connector.tools()
        names = {t.name for t in tools}
        assert {"add", "echo"} <= names  # advertised by the live server

        # The generated tools land in the agent's registry.
        registry = ConnectorRegistry()
        registry.register(connector)
        assert registry.into_tool_registry(ToolRegistry()) == len(tools)

        # Call tools live through the Chimera Tool interface (real tools/call RPC).
        add_tool = next(t for t in tools if t.name == "add")
        assert "5" in add_tool.run(a=2, b=3)

        echo_tool = next(t for t in tools if t.name == "echo")
        assert "hello-mcp" in echo_tool.run(text="hello-mcp")
    finally:
        connector._session.close()  # type: ignore[attr-defined]
