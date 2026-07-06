"""Chimera *as* an MCP server — expose solve / fuse / memory-search as MCP tools.

The interop bet (M12): MCP won the agent->tool layer, so the cheapest way to put Chimera
into every Claude/IDE/agent is to *be* a tool they already know how to call. Any MCP client
(Claude Desktop, an IDE, another agent) can then invoke ``chimera_solve`` and get an
autonomously-solved, verify-or-revert answer — the whole engine behind one tool call.

The design keeps the SDK at arm's length: :class:`ChimeraMCP` holds the tool *specs* and the
*dispatch* logic as plain Python (no ``mcp`` import), so the contract is unit-testable without
the optional dependency. :meth:`ChimeraMCP.build` / :meth:`serve_stdio` are the only parts that
touch the ``mcp`` SDK, imported lazily so the rest of Chimera never pays for it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Tool contract — pure data, so a test can assert the schema without the mcp SDK installed.
CHIMERA_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "chimera_solve",
        "description": (
            "Autonomously solve a task with Chimera's plan + verify-or-revert loop. "
            "Returns the final answer. Use for multi-step work, not a single Q&A turn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "The task to solve."}},
            "required": ["task"],
        },
    },
    {
        "name": "chimera_fuse",
        "description": (
            "Answer a prompt through Chimera's LLM-Fusion engine (panel -> judge -> "
            "synthesizer) for higher quality on hard reasoning. Returns the synthesized answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"prompt": {"type": "string", "description": "The prompt to fuse."}},
            "required": ["prompt"],
        },
    },
    {
        "name": "chimera_memory_search",
        "description": "Search Chimera's long-term memory and return the top matching facts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall."},
                "k": {"type": "integer", "description": "Max results (default 5).", "default": 5},
            },
            "required": ["query"],
        },
    },
]


@dataclass
class ChimeraMCP:
    """Bridges MCP tool calls to injected Chimera capabilities.

    The three callables are injected so the dispatch contract is testable with fakes and the
    heavy engines (autonomous agent, fusion, memory) are built only when actually serving.
    """

    solve: Callable[[str], str]
    fuse: Callable[[str], str]
    memory_search: Callable[[str, int], list[str]]

    def tool_specs(self) -> list[dict[str, Any]]:
        return CHIMERA_MCP_TOOLS

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """Route one MCP tool call to the matching capability; raise KeyError on unknown name."""
        if name == "chimera_solve":
            return self.solve(str(arguments["task"]))
        if name == "chimera_fuse":
            return self.fuse(str(arguments["prompt"]))
        if name == "chimera_memory_search":
            raw_k = arguments.get("k", 5)
            try:
                k = max(1, int(raw_k))
            except (TypeError, ValueError):
                k = 5
            hits = self.memory_search(str(arguments["query"]), k)
            return "\n".join(f"- {hit}" for hit in hits) if hits else "(no matching memories)"
        raise KeyError(name)

    def build(self) -> Any:
        """Construct the low-level ``mcp`` :class:`Server` wired to :meth:`dispatch`.

        Imports the ``mcp`` SDK lazily; raises ``ModuleNotFoundError`` (surfaced by the CLI as a
        friendly "pip install chimera-agent[mcp]") if the optional dependency is missing.
        """
        import mcp.types as types
        from mcp.server import Server

        server: Any = Server("chimera")
        specs = self.tool_specs()

        @server.list_tools()  # type: ignore[misc, no-untyped-call, untyped-decorator]
        async def _list_tools() -> list[Any]:
            return [types.Tool(**spec) for spec in specs]

        @server.call_tool()  # type: ignore[misc, no-untyped-call, untyped-decorator]
        async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
            text = self.dispatch(name, arguments or {})
            return [types.TextContent(type="text", text=text)]

        return server

    def serve_stdio(self) -> None:
        """Run the MCP server over stdio (blocking) — the standard local MCP transport."""
        import anyio
        from mcp.server.stdio import stdio_server

        server = self.build()

        async def _run() -> None:
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

        anyio.run(_run)
