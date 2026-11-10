"""Built-in tools reached on demand instead of declared up front.

Twenty-two tools are advertised on every step of every turn. Measured on this repository's own
registry, that is ~3,205 tokens of schema, re-sent each step, before the user has typed anything.

Measured on twenty-eight real sessions from an installed 0.48.0 — 33 tool calls in all — **four
tools account for every one of them**: `read_file` (14), `write_file` (10), `list_dir` (7),
`edit_file` (2). The eighteen never called cost ~2,745 tokens, **86% of the schema**.

`chimera.integrations.mcp_defer` already does this shape for MCP servers, and the reason it is off
by default is written there: the saving is in tokens, the risk is in selection accuracy, and a model
that has to look a tool up may choose worse than one handed the list. Nothing had measured that.

**This design does not take that risk with the tools that are actually used.** The core below —
files, search, shell — is declared in full, always. Only the rest is deferred. So for every one of
the 33 observed calls the behaviour is byte-identical to today, and the saving comes entirely from
schemas the model never reached for.

That is a claim about an observed sample, not a proof, and the sample is small and biased: 28
sessions of coding tasks, on one machine, by one person. A session that asks for a web page would
reach `scrape`, find it absent from the declared list, and have to go through `tool_describe` — one
extra round trip, not a dead end. `describe_saving` reports what deferral would save on the machine
that will pay for it, which is the only number that says anything about a given install.

Off by default, for the same reason the MCP one is.
"""

from __future__ import annotations

import json
from typing import Any

from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

_log = get_logger("tools.defer")

#: Declared in full, always. Every tool a coding turn reaches for without being asked twice.
#:
#: The first four are the whole of the observed use (33 of 33 calls across 28 sessions). The rest —
#: `glob`, `grep`, `apply_patch`, `run_shell` — are here because deferring them would trade tokens
#: for the one thing this must not cost: a turn that cannot do the obvious. They are cheap and they
#: are the tools a coding agent uses without deliberating.
CORE = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "glob",
        "grep",
        "apply_patch",
        "run_shell",
    }
)

#: Already proxies, so never moved behind another one. The Code turn registers them after this runs
#: and never met the question; the two chat surfaces mount MCP first, so with both switches on
#: `mcp_list`/`mcp_describe`/`mcp_call` were swept into `tool_list` — a round trip to reach a round
#: trip, which `code_api.assemble_registry` names as the thing its ordering exists to avoid. Kept
#: as a rule here rather than an ordering there, because an ordering is one more caller away from
#: being wrong again.
#: Built-ins a surface cannot reach through a proxy. Each one is handed a sink or a callback by the
#: surface that owns it (the chart and design canvases stream their frame, the browser its screenshot,
#: `ask_user` the question card, the Code screen its interpreter). Deferring one would route the turn's
#: own output through `tool_call` into a sink the proxy does not carry, so these stay declared.
TURN_BOUND = frozenset({
    "render_chart", "design", "browser", "ask_user", "execute_code", "code_interpreter",
    # The task cards: each is built over the turn's own tray (`TaskSuggestionTray`), which streams the
    # card to the screen. Routed through `tool_call` they would reach a tray the proxy does not carry.
    "suggest_task", "dismiss_task",
})

_NEVER_DEFERRED = CORE | frozenset({"mcp_list", "mcp_describe", "mcp_call"}) | TURN_BOUND


#: Every tool name `chimera.tools` defines (the unconditional ones and the conditional ones), without the
#: proxies, which are never moved, and without the test-only `echo`. `tests/test_the_product_defers_the_builtins_by_default.py`
#: re-derives this list from the source, so a built-in added without a line here fails the suite.
SHIPPED_BUILTINS: frozenset[str] = frozenset({
    "apply_patch", "arxiv_search", "ask_user", "browser", "calendar_events", "code_interpreter", "crawl",
    "create_document", "decide", "design", "download_media", "edit_batch", "edit_file", "execute_code",
    "extract", "generate_audio", "generate_image", "generate_video", "glob", "grep", "http_get", "job_cancel",
    "job_status", "list_dir", "map", "open_pull_request", "read_document", "read_email", "read_file",
    "recall_history", "render_chart", "report_defect", "run_shell", "schedule_once", "scrape", "send_email",
    "text_to_speech", "todo_write", "transcribe_audio", "view_image", "web_search", "write_file",
    "youtube_transcript",
})


def _unwrapped(tool: Tool) -> Tool:
    """The tool under the governance and ledger wrappers (`.inner`), so the module that defines it decides."""
    seen = 0
    current = tool
    while seen < 16 and isinstance(getattr(current, "inner", None), Tool):
        current = current.inner  # type: ignore[attr-defined]
        seen += 1
    return current


def _is_builtin(tool: Tool) -> bool:
    """A built-in the product ships: a name `chimera.tools` defines, from a tool that is not a connector.

    MCP and OpenAPI connector tools (`chimera.integrations`) carry their own switch (`CHIMERA_MCP_DEFER`),
    so a connector is never moved here even if its name collides with a built-in one. A name the product
    does not define (a plugin's, a caller's) stays declared too.
    """
    if tool.name not in SHIPPED_BUILTINS:
        return False
    return not type(_unwrapped(tool)).__module__.startswith("chimera.integrations")


#: How many lines `tool_list` will print before it says it stopped.
_MAX_LISTED = 60


def _one_line(text: str) -> str:
    return " ".join((text or "").split())[:160]


class _Deferred:
    """The tools this proxy may reach, and the rule for what it may not.

    The denial has to be re-applied HERE, and this is the security property of the whole module.
    The restriction filter matches registry names; after deferral those names are not in the
    registry, so a denylist naming `scrape` would silently stop removing it while `tool_call` could
    still run it. The MCP proxy documents the same hole about itself, and it is the reason both take
    the lists rather than trusting the filter that ran before them.

    The asymmetry is deliberate and matches the rest of the app: a caller may narrow itself with its
    own `allow_tools`, and may not raise the deployment's ceiling.
    """

    def __init__(
        self, tools: list[Tool], denied: frozenset[str], allowed: frozenset[str] | None
    ) -> None:
        self.denied = denied
        self.allowed = allowed
        self.by_name = {t.name: t for t in tools if self.permitted(t.name)}
        # The door the proxy runs through: the same `ToolRegistry.run` every other tool goes through, so
        # the owner's policy is re-read at CALL time (not only here, at build), and the span and the
        # `tool.ok` reading are the ones a declared tool gets. A second execution path is one more place
        # for a policy edited mid-conversation to be missed.
        self.runner = ToolRegistry()
        for tool in self.by_name.values():
            self.runner.register(tool)

    def reachable(self, name: str) -> Tool | None:
        """The deferred tool ``name`` if it is still permitted NOW, else ``None``."""
        tool = self.by_name.get(name)
        return tool if tool is not None and self.permitted(name) else None

    def permitted(self, name: str) -> bool:
        # The policy in its own terms (ordered globs, a later allow undoing an earlier deny of
        # the same file) — never as a reason to permit: the exact-name lists still apply.
        from chimera.deployment_policy import tool_denied

        if name in self.denied or tool_denied(name):
            return False
        return self.allowed is None or name in self.allowed


def _unknown(name: str, catalogue: _Deferred) -> str:
    """A miss that names the alternatives rather than only the failure.

    A bare "no such tool" leaves the model to guess whether it misspelled the name or the capability
    does not exist, and those want different next moves.
    """
    perto = sorted(n for n in catalogue.by_name if name and name.lower() in n.lower())
    if perto:
        return f"no tool named {name!r}. Did you mean: {', '.join(perto)}?"
    return f"no tool named {name!r}. Call tool_list to see what is available."


class ToolListTool(Tool):
    name = "tool_list"
    description = (
        "List the extra tools available beyond the file and shell tools you already have, one line "
        "each. Web, media, documents, charts and code execution live here. Optionally filter by a "
        "substring. Use this first, then tool_describe the one you want, then tool_call it."
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

    def __init__(self, catalogue: _Deferred) -> None:
        self.catalogue = catalogue

    def run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query") or "").strip().lower()
        rows = [
            f"{name}: {_one_line(tool.description)}"
            for name, tool in sorted(self.catalogue.by_name.items())
            if self.catalogue.permitted(name)
            if not query or query in name.lower() or query in (tool.description or "").lower()
        ]
        if not rows:
            return "no extra tool matches" if query else "no extra tools are available"
        shown, extra = rows[:_MAX_LISTED], len(rows) - _MAX_LISTED
        # Said, not truncated in silence: a list that stops without saying so reads as complete, and
        # the agent concludes the capability does not exist.
        tail = f"\n… and {extra} more; narrow the query" if extra > 0 else ""
        return "\n".join(shown) + tail


class ToolDescribeTool(Tool):
    name = "tool_describe"
    description = (
        "Show the full parameter schema of one deferred tool, by the exact name from tool_list. "
        "Call this before tool_call so the arguments match what the tool expects."
    )
    parameters = {
        "type": "object",
        "properties": {"tool": {"type": "string", "description": "Exact tool name from tool_list."}},
        "required": ["tool"],
    }

    def __init__(self, catalogue: _Deferred) -> None:
        self.catalogue = catalogue

    def run(self, **kwargs: Any) -> str:
        name = str(kwargs.get("tool") or "").strip()
        tool = self.catalogue.reachable(name)
        if tool is None:
            return _unknown(name, self.catalogue)
        return json.dumps(
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
            ensure_ascii=False,
            indent=2,
        )


class ToolCallTool(Tool):
    name = "tool_call"
    description = (
        "Run one deferred tool by its exact name from tool_list, passing its arguments as an object. "
        "Call tool_describe first unless you already know the schema."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "description": "Exact tool name from tool_list."},
            "arguments": {"type": "object", "description": "The tool's own arguments."},
        },
        "required": ["tool"],
    }

    def __init__(self, catalogue: _Deferred) -> None:
        self.catalogue = catalogue

    #: Unconditionally true, for the reason the MCP proxy states about itself. The taint layer reads
    #: this flag before anyone knows which tool the proxy will run, and the deferred set contains
    #: `scrape`, `crawl`, `browser` and `http_get` — every one of them a way for the open web to
    #: reach the model. Mirroring the target's own flag would be more precise and would resolve, at
    #: the moment it is read, to "unknown".
    untrusted_output = True

    def run(self, **kwargs: Any) -> str:
        name = str(kwargs.get("tool") or "").strip()
        if name not in self.catalogue.by_name:
            return _unknown(name, self.catalogue)
        argumentos = kwargs.get("arguments") or {}
        if not isinstance(argumentos, dict):
            return "arguments must be an object"
        if name in self.catalogue.denied or (
            self.catalogue.allowed is not None and name not in self.catalogue.allowed
        ):
            return _unknown(name, self.catalogue)
        # Through `ToolRegistry.run`: it applies the owner's deployment policy at call time (a policy
        # edited after deferral still denies), rejects undeclared arguments, and records the span.
        return self.catalogue.runner.run(name, **argumentos)

    def target_tool(self, name: str) -> Tool | None:
        """The deferred tool a call to ``name`` would run, for the layers that judge by the target."""
        return self.catalogue.by_name.get(name)


def defer_builtins(
    registry: ToolRegistry,
    *,
    denied: frozenset[str] = frozenset(),
    allowed: frozenset[str] | None = None,
) -> tuple[ToolRegistry, int]:
    """A registry holding the core plus three proxies, and how many tools were deferred.

    Returns a NEW registry rather than mutating: `restrict_registry` already builds one that way and
    every caller here holds the result, so a second convention would be one more thing to get wrong.

    The count is returned so a caller can log it. Zero means the registry had nothing outside the
    core, which is a different thing from deferral not running, and an intervention that cannot tell
    those apart reads as "on and useless" when it was "on and there was nothing to do".
    """
    deferidas = [t for t in registry.tools() if t.name not in _NEVER_DEFERRED and _is_builtin(t)]
    if not deferidas:
        return registry, 0

    catalogo = _Deferred(deferidas, denied, allowed)
    # Everything NOT moved stays declared: the core, the proxies, the turn-bound built-ins, and every tool
    # that is not a built-in (connectors, plugins, a caller's own). Keeping only `_NEVER_DEFERRED` here
    # would silently drop those, which is the bug this line used to have.
    moved = {tool.name for tool in deferidas}
    # Built directly, not through `restrict_registry`: that call names an allowlist, and an allowlist that
    # keeps a code-running tool is what the governance warning is FOR. Deferral is not an allowlist, so it
    # must not raise that alarm on the owner's behalf.
    enxuto = ToolRegistry.like(registry)
    for tool in registry.tools():
        if tool.name not in moved:
            enxuto.register(tool)
    # Only what the catalogue kept: a deferred tool the deployment fence denies is not grantable either,
    # or `declared_names()` would offer it to a subagent that the proxy itself refuses.
    enxuto.deferred.update(catalogo.by_name)
    for proxy in (ToolListTool(catalogo), ToolDescribeTool(catalogo), ToolCallTool(catalogo)):
        if proxy.name in enxuto:
            _log.warning("deferred tool %r collides with an existing tool — skipping", proxy.name)
            continue
        enxuto.register(proxy)

    _log.info("deferred %d built-in tools behind tool_list/tool_describe/tool_call", len(deferidas))
    return enxuto, len(deferidas)


def describe_saving(registry: ToolRegistry) -> dict[str, int]:
    """What deferral would save on THIS registry, in schema characters.

    Characters rather than tokens: the ratio is what matters, and a tokenizer would add a dependency
    to report the same proportion. Same choice, for the same reason, as the MCP version.
    """

    def medida(tool: Tool) -> int:
        return len(json.dumps(tool.to_openai_schema(), ensure_ascii=False))

    todas = list(registry.tools())
    nucleo = [t for t in todas if t.name in _NEVER_DEFERRED]
    deferidas = [t for t in todas if t.name not in _NEVER_DEFERRED]
    vazio = _Deferred([], frozenset(), None)
    proxies: list[Tool] = [ToolListTool(vazio), ToolDescribeTool(vazio), ToolCallTool(vazio)]
    return {
        "tools": len(todas),
        "core": len(nucleo),
        "deferred": len(deferidas),
        "declared_chars": sum(medida(t) for t in todas),
        "deferred_chars": sum(medida(t) for t in nucleo) + sum(medida(t) for t in proxies),
    }
