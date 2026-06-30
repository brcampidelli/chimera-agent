"""A tiny but real MCP server (FastMCP) served over stdio.

Spawned by ``tests/test_mcp_live.py`` to drive Chimera's live MCP client against a
genuine MCP handshake. Not a test module itself (pytest only collects ``test_*``).
Run standalone with: ``python tests/mcp_demo_server.py``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chimera-demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def echo(text: str) -> str:
    """Echo the given text back."""
    return text


if __name__ == "__main__":
    mcp.run()  # stdio transport by default
