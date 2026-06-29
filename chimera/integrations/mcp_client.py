"""MCP (Model Context Protocol) integration.

Two layers:

* a small, fully-tested *wrapping* layer that turns any MCP session (a thing that
  can ``list_tools`` and ``call_tool``) into Chimera tools, and
* :class:`StdioMCPSession`, a real stdio client backed by the optional ``mcp``
  package (install with the ``mcp`` extra). The heavy/async part is isolated here
  and lazily imported so the rest of Chimera never depends on it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from chimera.integrations.connectors import Connector
from chimera.telemetry import get_logger
from chimera.tools.base import Tool

_log = get_logger("integrations.mcp")


@dataclass
class MCPToolSpec:
    """A tool description advertised by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


class MCPSession(Protocol):
    """Anything that can list and call MCP tools (real or fake)."""

    def list_tools(self) -> list[MCPToolSpec]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


class MCPTool(Tool):
    """A Chimera tool that proxies to a tool on an MCP server."""

    def __init__(
        self,
        spec: MCPToolSpec,
        caller: Callable[[str, dict[str, Any]], str],
        *,
        name_prefix: str = "",
    ) -> None:
        self.name = f"{name_prefix}{spec.name}"
        self.description = spec.description
        self.parameters = spec.input_schema or {"type": "object", "properties": {}}
        self._remote_name = spec.name
        self._caller = caller

    def run(self, **kwargs: Any) -> str:
        return self._caller(self._remote_name, kwargs)


class MCPConnector(Connector):
    """Exposes an MCP server's tools as Chimera tools."""

    def __init__(self, name: str, session: MCPSession, *, name_prefix: str = "") -> None:
        self.name = name
        self._session = session
        self._name_prefix = name_prefix

    def tools(self) -> list[Tool]:
        return [
            MCPTool(spec, self._session.call_tool, name_prefix=self._name_prefix)
            for spec in self._session.list_tools()
        ]


def _content_to_text(result: Any) -> str:
    """Flatten an MCP CallToolResult's content blocks into text."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        parts.append(text if isinstance(text, str) else str(block))
    return "\n".join(parts)


class StdioMCPSession:
    """A live MCP session over stdio (requires the optional ``mcp`` package).

    Runs the async MCP client on a dedicated background event loop so the rest of
    Chimera can call ``list_tools``/``call_tool`` synchronously.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        *,
        connect_timeout: float = 30.0,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self.connect_timeout = connect_timeout
        self._loop: Any = None
        self._thread: Any = None
        self._session: Any = None
        self._stack: Any = None

    def start(self) -> StdioMCPSession:
        import asyncio
        import threading

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        future.result(timeout=self.connect_timeout)
        return self

    def _run_loop(self) -> None:
        import asyncio

        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=self.command, args=self.args, env=self.env)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        _log.debug("MCP stdio session connected: %s", self.command)

    def list_tools(self) -> list[MCPToolSpec]:
        import asyncio

        future = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop)
        response = future.result(timeout=self.connect_timeout)
        return [
            MCPToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
            )
            for tool in response.tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        import asyncio

        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop
        )
        result = future.result(timeout=120)
        return _content_to_text(result)

    def close(self) -> None:
        import asyncio

        if self._stack is not None and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop).result(timeout=10)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)


def connect_stdio(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    name_prefix: str = "",
) -> MCPConnector:
    """Connect to an MCP server over stdio and return a connector."""
    session = StdioMCPSession(command, args, env).start()
    return MCPConnector(name, session, name_prefix=name_prefix)
