"""Wire a real MCP server (GitHub) into the Chimera agent loop.

Run:
    uv sync --extra mcp                     # once: install the MCP client extra
    export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...   # a classic token, repo:read is enough
    uv run python examples/mcp_github.py

Needs Node.js (`npx`) and any model key (a free OpenRouter model works). The GitHub MCP
server's ~26 tools (search repos, read files, list issues, ...) join the built-in
registry and the agent uses them like any other tool.

No token? Do the zero-credential smoke test in docs/mcp.md instead (filesystem server).
"""

from __future__ import annotations

import os
import sys

from chimera.core import Agent, AgentConfig
from chimera.integrations import connect_stdio
from chimera.providers import LLMGateway
from chimera.tools import default_registry

TASK = "Use the GitHub tools to find the description of the repo brcampidelli/chimera-agent and summarize it in one sentence."


def main() -> int:
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        print("Set GITHUB_PERSONAL_ACCESS_TOKEN first (see the docstring).")
        return 1

    print("Starting the GitHub MCP server (first run downloads the package)...")
    connector = connect_stdio(
        "github",
        "npx",
        ["-y", "@modelcontextprotocol/server-github"],
        env={**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": token},
        name_prefix="gh_",
    )

    registry = default_registry()
    mcp_tools = connector.tools()
    for tool in mcp_tools:
        registry.register(tool)
    print(f"Registered {len(mcp_tools)} GitHub tools alongside the built-ins.\n")

    agent = Agent(LLMGateway(), registry, AgentConfig(max_steps=6))
    result = agent.run(TASK)
    print(f"\n=== answer ===\n{result.answer}")
    print(f"({result.stopped_reason}, {result.steps} steps, {result.tool_calls_made} tool calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
