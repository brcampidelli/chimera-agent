"""A minimal ReAct / tool-calling agent loop (Tier-1/Tier-2 seed).

The agent advertises its tools to a model backend and runs a Thought -> Action
(tool call) -> Observation loop until the model produces a final answer or the step
budget is exhausted. It depends only on the small :class:`SupportsComplete`
protocol, so any backend works — the single-model gateway today, the LLM-Fusion
engine in M2.

State is kept in an explicit transcript (not hidden in the model) — the first step
toward resisting continuous-evolution degradation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from chimera.providers.gateway import CompletionResult, MessageLike, SupportsComplete
from chimera.telemetry import get_logger
from chimera.tools.registry import ToolNotFoundError, ToolRegistry

_log = get_logger("core.agent")

DEFAULT_SYSTEM_PROMPT = (
    "You are Chimera, a capable autonomous agent. Break the task down, use the "
    "provided tools when they help, and verify your work. When you are confident the "
    "task is complete, reply with a concise final answer and stop calling tools."
)


@dataclass
class AgentConfig:
    """Tunable behaviour for an :class:`Agent` run."""

    model: str | None = None
    max_steps: int = 8
    temperature: float = 0.2
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


@dataclass
class AgentResult:
    """The outcome of an agent run."""

    answer: str
    steps: int
    stopped_reason: str  # "final" | "max_steps"
    transcript: list[MessageLike] = field(default_factory=list)
    tool_calls_made: int = 0


class Agent:
    """Runs a tool-calling loop against a model backend."""

    def __init__(
        self,
        backend: SupportsComplete,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
    ) -> None:
        self.backend = backend
        self.tools = tools
        self.config = config or AgentConfig()

    def run(self, task: str) -> AgentResult:
        messages: list[MessageLike] = [
            {"role": "system", "content": self.config.system_prompt},
            {"role": "user", "content": task},
        ]
        tool_schema = self.tools.to_openai_schema() or None
        tool_calls_made = 0

        for step in range(1, self.config.max_steps + 1):
            result = self.backend.complete(
                messages,
                model=self.config.model,
                temperature=self.config.temperature,
                tools=tool_schema,
            )
            if not result.tool_calls:
                messages.append({"role": "assistant", "content": result.content})
                return AgentResult(
                    answer=result.content,
                    steps=step,
                    stopped_reason="final",
                    transcript=messages,
                    tool_calls_made=tool_calls_made,
                )

            messages.append(self._assistant_tool_message(result))
            for call in result.tool_calls:
                tool_calls_made += 1
                observation = self._run_tool(call.name, call.arguments)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": observation}
                )

        # Budget exhausted: ask once more, without tools, for a final answer.
        final = self.backend.complete(
            [*messages, {"role": "user", "content": "Provide your final answer now."}],
            model=self.config.model,
            temperature=self.config.temperature,
            tools=None,
        )
        messages.append({"role": "assistant", "content": final.content})
        return AgentResult(
            answer=final.content,
            steps=self.config.max_steps,
            stopped_reason="max_steps",
            transcript=messages,
            tool_calls_made=tool_calls_made,
        )

    def _run_tool(self, name: str, arguments: dict[str, Any]) -> str:
        _log.debug("tool call %s(%s)", name, arguments)
        try:
            return self.tools.run(name, **arguments)
        except ToolNotFoundError:
            return f"error: unknown tool {name!r}"
        except Exception as exc:  # tools must never crash the loop
            _log.warning("tool %s failed: %s", name, exc)
            return f"error: tool {name!r} failed: {exc}"

    @staticmethod
    def _assistant_tool_message(result: CompletionResult) -> dict[str, Any]:
        calls = result.tool_calls or []
        return {
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in calls
            ],
        }
