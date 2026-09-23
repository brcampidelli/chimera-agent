"""The router's HINT mode — study 22, phase 6 (B4b).

B4 measured the narrowing router worse on every executor, and it broke both halves of the direction
rule at once: it removed tools and it could end the loop (`ANSWER`). The hint mode keeps only what the
rule allows, and this file holds each half: every tool stays on the wire; `ANSWER` is never offered and,
if a model says it anyway, nothing happens; the suggestion rides on one step and never enters the
history; the router sees the tools already used; and whether the executor followed is counted (§2r).
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core.tool_router import ANSWER, ToolRouter
from chimera.providers.gateway import CompletionResult, ToolCall

SCHEMAS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file."}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file."}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command."}},
]


class _Backend:
    """Records what it was asked, and answers with a fixed script."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.seen: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]], **kw: Any) -> CompletionResult:
        self.seen.append(messages)
        return CompletionResult(
            content=self.answers.pop(0) if self.answers else "read_file",
            model="openrouter/deepseek/deepseek-v4-flash-0731", prompt_tokens=100, completion_tokens=1,
        )


def test_a_mode_outside_the_two_is_refused() -> None:
    with pytest.raises(ValueError):
        ToolRouter(_Backend(), "m", mode="decide")


def test_hint_mode_never_offers_answer_and_ignores_it_when_said() -> None:
    backend = _Backend(ANSWER)
    router = ToolRouter(backend, "cheap/model", mode="hint")
    assert router.pick("is it done?", [], SCHEMAS) is None
    system = backend.seen[0][0]["content"]
    assert ANSWER not in system and "decides for itself" in system
    assert router.stats.answered == 0 and router.stats.fallbacks == 1 and router.stats.hinted == 0


def test_hint_mode_shows_the_router_the_tools_already_used() -> None:
    backend = _Backend("write_file")
    router = ToolRouter(backend, "cheap/model", mode="hint")
    history = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "read_file"}}]},
        {"role": "tool", "content": "contents"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "2", "type": "function", "function": {"name": "run_shell"}}]},
    ]
    assert router.pick("fix it", history, SCHEMAS) == "write_file"
    assert "Tools used so far: read_file, run_shell" in backend.seen[0][1]["content"]
    assert router.stats.hinted == 1


def _agent_with(router: ToolRouter, first_call: str | None) -> tuple[list[Any], list[Any]]:
    from chimera.core import Agent, AgentConfig
    from chimera.tools import ToolRegistry
    from chimera.tools.base import Tool

    class _Read(Tool):
        name = "read_file"
        description = "Read a UTF-8 text file."
        parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

        def run(self, **kwargs: Any) -> str:
            return "contents"

    class _Shell(Tool):
        name = "run_shell"
        description = "Run a shell command."
        parameters = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}

        def run(self, **kwargs: Any) -> str:
            return "ok"

    seen_tools: list[Any] = []
    seen_messages: list[Any] = []

    class _Executor:
        calls = 0

        def complete(self, messages: list[dict[str, Any]], **kw: Any) -> Any:
            seen_tools.append(kw.get("tools"))
            seen_messages.append(list(messages))
            _Executor.calls += 1
            if _Executor.calls == 1 and first_call:
                args = {"path": "a"} if first_call == "read_file" else {"command": "ls"}
                return CompletionResult(content="", model="exec", prompt_tokens=1, completion_tokens=1,
                                        tool_calls=[ToolCall(id="c1", name=first_call, arguments=args)])
            return CompletionResult(content="done", model="exec", prompt_tokens=1, completion_tokens=1)

    registry = ToolRegistry()
    registry.register(_Read())
    registry.register(_Shell())
    agent = Agent(_Executor(), registry, AgentConfig(model="exec", max_steps=3, tool_router=router))
    agent.run("read the file")
    return seen_tools, seen_messages


def test_the_loop_keeps_every_tool_and_the_hint_rides_on_one_step_only() -> None:
    router = ToolRouter(_Backend("read_file", "read_file"), "cheap/model", mode="hint")
    tools, messages = _agent_with(router, "read_file")
    assert [s["function"]["name"] for s in tools[0]] == ["read_file", "run_shell"]  # nothing removed
    hints = [m for m in messages[0] if "Hint from a fast router" in str(m.get("content"))]
    assert len(hints) == 1
    # The next step's history holds the call and its result — and not the previous step's hint.
    later_hints = [m for m in messages[1] if "Hint from a fast router" in str(m.get("content"))]
    assert len(later_hints) == 1  # only the new step's own hint
    assert router.stats.hinted >= 1 and router.stats.followed == 1


def test_a_hint_the_executor_does_not_take_is_counted_as_not_followed() -> None:
    router = ToolRouter(_Backend("read_file", "read_file"), "cheap/model", mode="hint")
    _agent_with(router, "run_shell")
    assert router.stats.hinted >= 1 and router.stats.followed == 0


def test_narrow_mode_is_unchanged() -> None:
    router = ToolRouter(_Backend("read_file"), "cheap/model")
    tools, _ = _agent_with(router, None)
    assert [s["function"]["name"] for s in tools[0]] == ["read_file"]
