"""Generic subagent delegation — spawn a fresh, tool-scoped Agent for a subtask.

Generalises the Context Explorer pattern into an arbitrary-subtask tool: the main agent
delegates a self-contained subtask to a subagent that runs its OWN loop, with only an allowed
subset of tools, in its OWN context — and gets back ONLY the final result, not the subagent's
transcript. This keeps the main agent's context focused and lets work fan out. Two guardrails:
recursion is disabled (a subagent is never granted the spawn tool), and a subagent can never
exceed the configured tool allowlist (so it can't escalate past what the caller was given).
"""

from __future__ import annotations

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
        registry: ToolRegistry,
        *,
        allowed: list[str] | None = None,
        model: str | None = None,
        max_turns: int = 8,
    ) -> None:
        self._backend = backend
        self._registry = registry
        base = set(allowed) if allowed is not None else set(registry.names())
        base.discard(self.name)  # never grant the spawn tool itself -> no recursion
        self._allowed = base
        self._model = model
        self._max_turns = max_turns

    def _build_registry(self, requested: list[str] | None) -> ToolRegistry:
        """The sub-registry: requested names ∩ allowlist (or the whole allowlist)."""
        names = (set(requested) & self._allowed) if requested else set(self._allowed)
        sub = ToolRegistry()
        for name in sorted(names):
            if name in self._registry:
                sub.register(self._registry.get(name))
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
