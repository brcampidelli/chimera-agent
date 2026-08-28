"""MCP tools reached on demand instead of declared up front.

Every connected MCP server's tools are registered with their full JSON schema, and that schema is
re-sent to the model on **every step of every turn**. A handful of servers is a few thousand tokens
on a call that may not use any of them; a catalogue is a standing tax on the whole session. The
comparable product that measured this reported **−46.9% of total agent tokens** in a significant A/B
after serving MCP as something the agent opens rather than something it is handed.

This is that shape: three tools replace N.

* ``mcp_list`` — what is available, one line each. The catalogue, without the schemas.
* ``mcp_describe`` — the full schema of one tool, when the agent has decided to use it.
* ``mcp_call`` — run it.

**Off by default.** The saving is in tokens and the risk is in selection accuracy — a model that has
to search for a tool may pick worse than one handed the list, and nothing here has measured that.
Turning it on by conviction is what this project has rules against; `describe_saving` exists so the
first half can be measured on the machine that will pay for it, and the second half is named in the
release notes as unmeasured rather than assumed away.

**Two safety properties, and neither is optional.**

Denial has to be re-applied *here*. The restriction filter works on registry names, and after
deferral the N names are not in the registry — a denylist naming an MCP tool would stop removing it
while `mcp_call` could still reach it. That is the same hole `ExploreRepositoryTool` documents about
itself for being registered after the filter, and it is worse here because one proxy reaches
everything.

And ``untrusted_output`` is True on the proxy, unconditionally. MCP output is external content; the
taint layer keys fencing and run-tainting off that flag, and it is read before anyone knows which
tool the proxy will call. Mirroring the target's flag would be more precise and would resolve, at
the moment it matters, to "unknown".
"""

from __future__ import annotations

import json
from typing import Any

from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

_log = get_logger("integrations.mcp_defer")

#: How many tools one `mcp_list` answer may name. A catalogue larger than this is exactly the case
#: deferral exists for, and dumping all of it back into the context would undo the saving.
_MAX_LISTED = 60


def _one_line(text: str) -> str:
    """First sentence-ish of a description. The catalogue is for choosing, not for reading."""
    flat = " ".join((text or "").split())
    return flat[:160]


class _McpTools:
    """The set of MCP tools this proxy may reach, and the rule for what it may not."""

    def __init__(self, tools: list[Tool], denied: frozenset[str], allowed: frozenset[str] | None):
        self.denied = denied
        self.allowed = allowed
        self.by_name = {t.name: t for t in tools if self.permitted(t.name)}

    def permitted(self, name: str) -> bool:
        if name in self.denied:
            return False
        return self.allowed is None or name in self.allowed


class McpListTool(Tool):
    name = "mcp_list"
    description = (
        "List the tools available from connected MCP servers, one line each. Optionally filter by "
        "a substring of the name or description. Use this first, then mcp_describe the one you want."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Substring filter over names and descriptions. Empty lists everything.",
            }
        },
    }

    def __init__(self, catalogue: _McpTools) -> None:
        self.catalogue = catalogue

    def run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query") or "").strip().lower()
        rows = [
            f"{name}: {_one_line(tool.description)}"
            for name, tool in sorted(self.catalogue.by_name.items())
            if not query or query in name.lower() or query in (tool.description or "").lower()
        ]
        if not rows:
            return "no MCP tool matches" if query else "no MCP tools are connected"
        shown, extra = rows[:_MAX_LISTED], len(rows) - _MAX_LISTED
        # Said rather than truncated in silence: a list that stops without saying so reads as a
        # complete answer, and the agent concludes the tool it needs does not exist.
        tail = f"\n… and {extra} more; narrow the query" if extra > 0 else ""
        return "\n".join(shown) + tail


class McpDescribeTool(Tool):
    name = "mcp_describe"
    description = (
        "Show the full parameter schema of one MCP tool, by the exact name from mcp_list. "
        "Call this before mcp_call so the arguments match what the server expects."
    )
    parameters = {
        "type": "object",
        "properties": {"tool": {"type": "string", "description": "Exact tool name from mcp_list."}},
        "required": ["tool"],
    }

    def __init__(self, catalogue: _McpTools) -> None:
        self.catalogue = catalogue

    def run(self, **kwargs: Any) -> str:
        name = str(kwargs.get("tool") or "").strip()
        tool = self.catalogue.by_name.get(name)
        if tool is None:
            return _unknown(name, self.catalogue)
        return json.dumps(
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
            ensure_ascii=False,
            indent=2,
        )


class McpCallTool(Tool):
    name = "mcp_call"
    description = (
        "Run one MCP tool by its exact name, with its arguments as an object. "
        "Get the name from mcp_list and the argument shape from mcp_describe."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "description": "Exact tool name from mcp_list."},
            "arguments": {"type": "object", "description": "Arguments, shaped as mcp_describe says."},
        },
        "required": ["tool"],
    }
    #: Unconditionally. See the module docstring: this is read before anyone knows which tool the
    #: proxy will call, so the precise answer is not available at the moment the flag is needed.
    untrusted_output = True

    def __init__(self, catalogue: _McpTools) -> None:
        self.catalogue = catalogue

    def run(self, **kwargs: Any) -> str:
        name = str(kwargs.get("tool") or "").strip()
        tool = self.catalogue.by_name.get(name)
        if tool is None:
            return _unknown(name, self.catalogue)
        raw = kwargs.get("arguments") or {}
        if isinstance(raw, str):
            # Models send this field as a JSON string often enough that refusing it would spend a
            # turn on a formatting round-trip rather than on the task.
            try:
                raw = json.loads(raw)
            except ValueError:
                return "error: `arguments` must be an object (or a JSON object as a string)"
        if not isinstance(raw, dict):
            return "error: `arguments` must be an object"
        return tool.run(**raw)


def _unknown(name: str, catalogue: _McpTools) -> str:
    """One message for both refusals, and it never says whether the name exists.

    A denied tool and an absent one answer identically on purpose. Distinguishing them would turn
    the proxy into an oracle for what an owner's denylist contains, which is a fact a run reading
    untrusted content has no business being able to probe.
    """
    return f"error: no MCP tool named {name!r} is available; call mcp_list to see what is"


def register_deferred_mcp(
    pool: Any,
    registry: ToolRegistry,
    *,
    denied: frozenset[str] = frozenset(),
    allowed: frozenset[str] | None = None,
) -> int:
    """Register the three access tools in place of the pool's N. Returns how many were deferred.

    ``denied``/``allowed`` are re-applied here rather than left to the registry filter, because
    after deferral the filter cannot see the names — see the module docstring.
    """
    catalogue = _McpTools(list(pool.all_tools()), denied, allowed)
    if not catalogue.by_name:
        return 0
    for tool in (McpListTool(catalogue), McpDescribeTool(catalogue), McpCallTool(catalogue)):
        if tool.name in registry:
            _log.warning("deferred MCP tool %r collides with an existing tool — skipping", tool.name)
            continue
        registry.register(tool)
    return len(catalogue.by_name)


def describe_saving(pool: Any) -> dict[str, int]:
    """What deferral would save on THIS machine's servers, in schema characters.

    Measured rather than quoted. The −46.9% that motivates this feature was measured on somebody
    else's harness with somebody else's servers, and the only number that says anything about your
    session is the one taken from your own `mcp.json`. Characters, not tokens: the ratio is what
    matters and a tokenizer would add a dependency to report the same proportion.
    """
    def measure(tool: Tool) -> int:
        return len(json.dumps(tool.to_openai_schema(), ensure_ascii=False))

    empty = _McpTools([], frozenset(), None)
    tools = list(pool.all_tools())
    proxies: list[Tool] = [McpListTool(empty), McpDescribeTool(empty), McpCallTool(empty)]
    return {
        "tools": len(tools),
        "declared_chars": sum(measure(t) for t in tools),
        "deferred_chars": sum(measure(t) for t in proxies),
    }
