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
from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

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
        names = (set(requested) & allowed) if requested else set(allowed)
        source = self._current()
        sub = ToolRegistry()
        for name in sorted(names):
            if name in source:
                sub.register(source.get(name))
        return sub

    def run(self, **kwargs: Any) -> str:
        task = str(kwargs.get("task", "")).strip()
        if not task:
            return "error: task is required"
        requested = kwargs.get("tools")
        sub = self._build_registry(requested if isinstance(requested, list) else None)
        agent = Agent(
            self._backend,
            sub,
            AgentConfig(
                model=self._model,
                max_steps=self._max_turns,
                temperature=0.2,
                system_prompt=SUBAGENT_SYSTEM,
            ),
        )
        result = agent.run(task)  # transcript stays here; only the answer is returned
        _log.debug("subagent finished in %d step(s), %d tool call(s)", result.steps, result.tool_calls_made)
        return result.answer
