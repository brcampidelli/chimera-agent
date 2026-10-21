"""Generic subagent delegation — spawn a fresh, tool-scoped Agent for a subtask.

Generalises the Context Explorer pattern into an arbitrary-subtask tool: the main agent
delegates a self-contained subtask to a subagent that runs its OWN loop, with only an allowed
subset of tools, in its OWN context — and gets back ONLY the final result, not the subagent's
transcript. This keeps the main agent's context focused and lets work fan out. Two guardrails:
recursion is disabled (a subagent is never granted the spawn tool), and a subagent can never
exceed the configured tool allowlist (so it can't escalate past what the caller was given).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from chimera.core.agent import Agent, AgentConfig, AgentResult, run_nested
from chimera.governance.sanitize import fence, sanitize_untrusted
from chimera.orchestration.hierarchy import CUT_OFF_REASONS
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from chimera.core.markdown_agents import MarkdownAgent

_log = get_logger("core.subagent")

SUBAGENT_SYSTEM = (
    "You are a subagent handling ONE self-contained subtask delegated by a main agent. Use the "
    "available tools to complete it, then reply with a concise result the main agent can use "
    "directly. Do not ask questions — work with what you are given and finish."
)


class SubAgentTool(Tool):
    """Lets the main agent delegate a subtask to an isolated, tool-scoped subagent."""

    name = "spawn_subagent"
    description = (
        "Delegate a self-contained subtask to a fresh subagent that runs in its own context "
        "with a chosen subset of tools and returns ONLY its final result. Use this to keep your "
        "own context focused, or to fan work out. The subagent cannot itself spawn subagents."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The self-contained subtask to delegate."},
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tool names to grant the subagent (a subset of yours). Omit to grant all allowed.",
            },
            "agent": {
                "type": "string",
                "description": "Optional named Markdown subagent from this project or the owner's agent folder.",
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        backend: SupportsComplete,
        registry: ToolRegistry | Callable[[], ToolRegistry],
        *,
        allowed: list[str] | None = None,
        model: str | None = None,
        max_turns: int = 8,
        markdown_agents: dict[str, MarkdownAgent] | None = None,
    ) -> None:
        self._backend = backend
        #: The registry to draw the subagent's tools from — resolved LATE when it is a callable.
        #:
        #: This is the fix for a real hole. A caller builds the registry, registers this tool with
        #: it, and only THEN wraps the registry in the governance and taint layers, rebinding its own
        #: variable to the wrapper. The tool kept a reference to the raw object, so a subagent ran
        #: with ungoverned, untainted tools — the one path in the system that could spawn work
        #: outside every guard the parent was under. Passing ``lambda: registry`` closes it, because
        #: a closure over a rebound variable sees the FINAL value, which is exactly what is wanted
        #: here and is usually the bug rather than the fix.
        self._source = registry
        self._allowed_names = set(allowed) if allowed is not None else None
        self._model = model
        self._max_turns = max_turns
        self._markdown_agents = dict(markdown_agents or {})
        if self._markdown_agents:
            # Only the slugs reach the tool description: a file's `description` is project text,
            # and a tool description is read with more authority than a fenced data block.
            self.description = (
                f"{type(self).description} Named agents (pass as `agent`): "
                f"{', '.join(sorted(self._markdown_agents))}."
            )
        #: What the last delegation's run spent. Inside a run it is on that run's bill already;
        #: this is how a caller that ran the tool on its own reads it.
        self.last_spend: AgentResult | None = None

    def _current(self) -> ToolRegistry:
        return self._source() if callable(self._source) else self._source

    @property
    def _allowed(self) -> set[str]:
        """Resolved per call, not at construction — the allowlist has to follow the same late
        binding, or a subagent would be granted names from a registry it no longer draws from."""
        registry = self._current()
        base = set(self._allowed_names) if self._allowed_names is not None else set(registry.names())
        base.discard(self.name)  # never grant the spawn tool itself -> no recursion
        return base

    def _build_registry(self, requested: list[str] | None) -> ToolRegistry:
        """The sub-registry: requested names ∩ allowlist (or the whole allowlist)."""
        allowed = self._allowed
        return self._registry_of((set(requested) & allowed) if requested else set(allowed))

    def _registry_of(self, names: set[str]) -> ToolRegistry:
        """Exactly ``names`` from the late-bound (governed) registry; empty means no tools."""
        source = self._current()
        sub = ToolRegistry.like(source)  # the parent's skill scope comes along with its tools
        for name in sorted(names):
            if name in source:
                sub.register(source.get(name))
        return sub

    def run(self, **kwargs: Any) -> str:
        task = str(kwargs.get("task", "")).strip()
        if not task:
            return "error: task is required"
        requested = kwargs.get("tools")
        requested_tools = requested if isinstance(requested, list) else None
        selected_name = str(kwargs.get("agent", "")).strip()
        selected = self._markdown_agents.get(selected_name) if selected_name else None
        if selected_name and selected is None:
            return f"error: unknown subagent {selected_name!r}"
        if selected is None:
            sub = self._build_registry(requested_tools)
            model = self._model
            max_steps = self._max_turns
            system_prompt = SUBAGENT_SYSTEM
        else:
            # The Markdown allowlist is intersected with the parent's already-governed registry;
            # neither project nor owner files can add a tool or reach the raw registry.
            session_agent = selected.for_session(self._allowed)
            effective = session_agent.definition
            selected_tools = set(session_agent.effective_tools)
            if requested_tools is not None:
                selected_tools.intersection_update(requested_tools)
            # An empty intersection must stay empty. `_build_registry([])` means "all tools" for
            # legacy callers, so the named path never goes through it.
            sub = self._registry_of(selected_tools & self._allowed)
            model = selected.model or self._model
            max_steps = min(selected.steps, self._max_turns)
            system_prompt = f"{SUBAGENT_SYSTEM}\n\n{effective.instructions}"
        agent = Agent(
            self._backend,
            sub,
            AgentConfig(
                model=model,
                max_steps=max_steps,
                temperature=0.2,
                system_prompt=system_prompt,
            ),
        )
        # The transcript stays here; only the answer is returned. What the run spent goes on the
        # bill of the run that delegated, on its ceiling (`run_nested`), and is kept for a caller
        # that ran this tool on its own.
        result = run_nested("the sub-agent", lambda spend: agent.run(task, spend=spend))
        self.last_spend = result
        _log.debug("subagent finished in %d step(s), %d tool call(s)", result.steps, result.tool_calls_made)
        # These are report data for the parent, not verified task findings. Keep the stop state
        # explicit, and mark ceilings as incomplete so a partial answer cannot read as finished.
        reason = sanitize_untrusted(result.stopped_reason or "unknown")
        status = " INCOMPLETE" if result.stopped_reason in CUT_OFF_REASONS else ""
        summary = (
            f"Subagent status: {reason}{status}; steps: {result.steps}; "
            f"tool calls: {result.tool_calls_made}."
        )
        return f"{summary}\n\n{fence(sanitize_untrusted(result.answer))}"
