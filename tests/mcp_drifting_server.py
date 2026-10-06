"""A real MCP server (FastMCP, stdio) whose one tool description is read from a file at start.

Spawned by ``tests/test_a_changed_mcp_manifest_is_held_until_approved.py`` to show a server that
says something different on its second connect. Not a test module (pytest collects ``test_*``).
Usage: ``python tests/mcp_drifting_server.py <description-file>``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chimera-drift")


def echo(text: str) -> str:
    return text


mcp.add_tool(echo, name="echo", description=Path(sys.argv[1]).read_text(encoding="utf-8"))

if __name__ == "__main__":
    mcp.run()  # stdio transport by default
