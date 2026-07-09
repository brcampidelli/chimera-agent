"""Tests for the core ReAct agent loop using a scripted fake backend."""

from __future__ import annotations

from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


class ScriptedBackend:
    """Returns queued responses; once empty, answers a forced-final call."""

    def __init__(self, responses: list[CompletionResult], final: str = "final answer") -> None:
        self._responses = list(responses)
        self.final = final
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        self.calls.append({"tools": tools, "messages": list(messages)})
        if self._responses:
            return self._responses.pop(0)
        return CompletionResult(content=self.final, model="fake")


def test_looks_like_unexecuted_plan_heuristic() -> None:
    from chimera.core.agent import _looks_like_unexecuted_plan

    assert _looks_like_unexecuted_plan("You can run:\n```bash\ngit merge x\n```") is True
    assert _looks_like_unexecuted_plan("To fix this, run git checkout main") is True  # advisory phrase
    assert _looks_like_unexecuted_plan("you should run the migration") is True
    assert _looks_like_unexecuted_plan("I applied the merge; the change is now on master.") is False
    assert _looks_like_unexecuted_plan("The bug is in line 5. I fixed it and tests pass.") is False


def _skill_registry_with_echo() -> Any:
    from chimera.skills.builtin.echo_skill import EchoSkill
    from chimera.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.register(EchoSkill())
    return reg


def test_run_injects_relevant_skill_context() -> None:
    # A task whose keywords match a skill's name/description gets that skill surfaced in the system prompt.
    backend = ScriptedBackend([CompletionResult(content="done", model="fake")])
    agent = Agent(backend, _echo_registry(), AgentConfig(), skills=_skill_registry_with_echo())
    agent.run("please echo this message back to me")
    system = backend.calls[0]["messages"][0]
    assert system["role"] == "system"
    assert "Relevant skills you can use:" in system["content"] and "echo" in system["content"]


def test_skill_context_absent_when_no_match_or_disabled() -> None:
    reg = _skill_registry_with_echo()
    # No keyword overlap -> no block injected.
    b1 = ScriptedBackend([CompletionResult(content="done", model="fake")])
    Agent(b1, _echo_registry(), AgentConfig(), skills=reg).run("xyzzy plugh frobnicate quux")
    assert "Relevant skills" not in b1.calls[0]["messages"][0]["content"]
    # Explicitly disabled -> no block even with a matching skill.
    b2 = ScriptedBackend([CompletionResult(content="done", model="fake")])
    Agent(b2, _echo_registry(), AgentConfig(inject_skill_context=False), skills=reg).run("echo this")
    assert "Relevant skills" not in b2.calls[0]["messages"][0]["content"]


def test_insist_on_action_pushes_back_a_described_plan() -> None:
    # Narration first (a code block, no tool call), then the model acts, then reports done.
    backend = ScriptedBackend(
        [
            CompletionResult(content="You can run:\n```bash\ngit merge x\n```", model="fake"),
            CompletionResult(
                content="", model="fake",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "did it"})],
            ),
            CompletionResult(content="Done — the merge is applied.", model="fake"),
        ]
    )
    agent = Agent(backend, _echo_registry(), AgentConfig(insist_on_action=True))
    result = agent.run("merge the lost commit")
    assert result.answer == "Done — the merge is applied."
    assert result.tool_calls_made == 1  # the nudge made it actually act
    assert any("did not carry it out" in str(m.get("content", "")) for m in result.transcript if isinstance(m, dict))


def test_insist_off_by_default_accepts_narration() -> None:
    backend = ScriptedBackend([CompletionResult(content="You can run:\n```\nfoo\n```", model="fake")])
    result = Agent(backend, _echo_registry()).run("do a thing")  # insist_on_action defaults False
    assert result.answer.startswith("You can run")  # returned as-is, no nudge


def test_insist_accepts_a_completion_report() -> None:
    # A real "I did it" report (no code block, no advisory phrasing) is accepted, not nudged.
    backend = ScriptedBackend([CompletionResult(content="I created hello.py and it passes.", model="fake")])
    result = Agent(backend, _echo_registry(), AgentConfig(insist_on_action=True)).run("make hello.py")
    assert result.answer == "I created hello.py and it passes."
    assert result.steps == 1  # accepted on the first response, no nudge round


def test_insist_nudges_at_most_once() -> None:
    # Two narrations in a row: after one nudge the second is accepted (no infinite loop).
    backend = ScriptedBackend(
        [
            CompletionResult(content="```\nplan a\n```", model="fake"),
            CompletionResult(content="```\nplan b\n```", model="fake"),
        ]
    )
    result = Agent(backend, _echo_registry(), AgentConfig(insist_on_action=True)).run("act")
    assert result.answer == "```\nplan b\n```"  # second narration accepted; loop did not hang


def test_agent_runs_tool_then_finalizes() -> None:
    backend = ScriptedBackend(
        [
            CompletionResult(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "ping"})],
            ),
            CompletionResult(content="done", model="fake"),
        ]
    )
    agent = Agent(backend, _echo_registry())
    result = agent.run("say ping then finish")

    assert result.stopped_reason == "final"
    assert result.answer == "done"
    assert result.tool_calls_made == 1
    assert result.steps == 2
    # the tool observation "ping" is in the transcript
    assert any(m.get("content") == "ping" for m in result.transcript if isinstance(m, dict))


def test_agent_hits_max_steps_and_forces_answer() -> None:
    always_tool = CompletionResult(
        content="",
        model="fake",
        tool_calls=[ToolCall(id="c", name="echo", arguments={"text": "x"})],
    )

    class AlwaysToolBackend:
        def __init__(self) -> None:
            self.final_calls = 0

        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
            if tools is None:  # the forced-final call
                self.final_calls += 1
                return CompletionResult(content="forced", model="fake")
            return always_tool

    backend = AlwaysToolBackend()
    agent = Agent(backend, _echo_registry(), AgentConfig(max_steps=3))
    result = agent.run("loop forever")

    assert result.stopped_reason == "max_steps"
    assert result.answer == "forced"
    assert result.steps == 3
    assert backend.final_calls == 1


def test_agent_handles_unknown_tool_gracefully() -> None:
    backend = ScriptedBackend(
        [
            CompletionResult(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="c1", name="does_not_exist", arguments={})],
            ),
            CompletionResult(content="recovered", model="fake"),
        ]
    )
    agent = Agent(backend, _echo_registry())
    result = agent.run("call a missing tool")

    assert result.answer == "recovered"
    assert any(
        isinstance(m, dict) and "unknown tool" in str(m.get("content", ""))
        for m in result.transcript
    )
