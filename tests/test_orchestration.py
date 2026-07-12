"""Tests for multi-agent orchestration (roles, MOC comms, crews)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.memory import MemoryManager, MemoryStore
from chimera.orchestration import (
    AgentMessage,
    Role,
    RoleAgent,
    SequentialCrew,
    SupervisorCrew,
    consolidate,
    parallel_review,
    render,
)
from chimera.providers import CompletionResult


class RoleBackend:
    """Returns the system prompt verbatim, so each role's output is identifiable."""

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        system = ""
        for message in messages:
            data = message.as_dict() if hasattr(message, "as_dict") else message
            if data.get("role") == "system":
                system = str(data.get("content", ""))
                break
        return CompletionResult(content=system, model="fake")


# --- comms ------------------------------------------------------------------

def test_consolidate_merges_near_duplicates() -> None:
    messages = [
        AgentMessage("a", "the cat sat on the mat"),
        AgentMessage("b", "the cat sat on a mat"),  # near-duplicate of a
        AgentMessage("c", "dogs run very fast outdoors"),
    ]
    kept = consolidate(messages, threshold=0.8)
    assert len(kept) == 2
    # the later phrasing of the duplicate pair is kept
    assert kept[0].sender == "b"
    assert kept[1].sender == "c"


def test_render_format() -> None:
    out = render([AgentMessage("x", "hello")])
    assert out == "[x] hello"


# --- roles ------------------------------------------------------------------

def test_role_agent_acts_in_character() -> None:
    agent = RoleAgent(Role("planner", "SYSTEM-PLANNER"), RoleBackend())
    assert agent.name == "planner"
    assert agent.act("do the thing") == "SYSTEM-PLANNER"


class WorkerBackend:
    """First call requests a tool (if tools are offered); then returns a final answer."""

    def __init__(self) -> None:
        self.n = 0
        self.used_tools = False

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        from chimera.providers.gateway import ToolCall

        self.n += 1
        if self.n == 1 and kwargs.get("tools"):
            self.used_tools = True
            return CompletionResult(
                content="", model="fake",
                tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "WORKED"})],
            )
        return CompletionResult(content="final:done", model="fake")


def test_role_agent_with_tools_runs_a_real_loop() -> None:
    from chimera.tools import EchoTool, ToolRegistry

    reg = ToolRegistry()
    reg.register(EchoTool())
    backend = WorkerBackend()
    agent = RoleAgent(Role("worker", "WORK"), backend, tools=reg)
    assert agent.act("do it") == "final:done"
    assert backend.used_tools  # it actually executed a tool call, not a single-shot reply


def test_tool_using_worker_inside_a_crew() -> None:
    from chimera.tools import EchoTool, ToolRegistry

    reg = ToolRegistry()
    reg.register(EchoTool())
    crew = SequentialCrew([RoleAgent(Role("doer", "DO"), WorkerBackend(), tools=reg)])
    assert crew.run("task").answer == "final:done"


# --- sequential crew --------------------------------------------------------

def test_sequential_crew_runs_in_order() -> None:
    agents = [RoleAgent(Role("a", "SP-A"), RoleBackend()), RoleAgent(Role("b", "SP-B"), RoleBackend())]
    result = SequentialCrew(agents).run("task")
    assert [m.content for m in result.transcript] == ["SP-A", "SP-B"]
    assert [m.sender for m in result.transcript] == ["a", "b"]
    assert result.answer == "SP-B"


def test_sequential_crew_writes_shared_memory(tmp_path: Path) -> None:
    manager = MemoryManager(MemoryStore(tmp_path / "mem.json"))
    agents = [RoleAgent(Role("a", "SP-A"), RoleBackend()), RoleAgent(Role("b", "SP-B"), RoleBackend())]
    SequentialCrew(agents, shared_memory=manager).run("task")
    assert len(manager.store) == 2


# --- supervisor crew --------------------------------------------------------

def test_supervisor_crew_synthesizes() -> None:
    supervisor = RoleAgent(Role("boss", "BOSS"), RoleBackend())
    workers = [
        RoleAgent(Role("w1", "WORKER-1"), RoleBackend()),
        RoleAgent(Role("w2", "WORKER-2"), RoleBackend()),
    ]
    result = SupervisorCrew(supervisor, workers).run("task")
    assert result.answer == "BOSS"
    senders = {m.sender for m in result.transcript}
    assert {"boss", "w1", "w2"} <= senders


def test_parallel_review() -> None:
    reviewers = [
        RoleAgent(Role("r1", "R1"), RoleBackend()),
        RoleAgent(Role("r2", "R2"), RoleBackend()),
    ]
    messages = parallel_review(reviewers, "subject")
    assert {m.sender for m in messages} == {"r1", "r2"}
    assert parallel_review([], "x") == []


# --- 13th adversarial review: crew resilience + honest consensus + tool allowlist --------


class _BoomBackend:
    """A backend that always fails — stands in for a transient provider error."""

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        raise RuntimeError("provider down")


class _EchoUserBackend:
    """Echoes the user message (context + task) back, so we can inspect what an agent received."""

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        user = ""
        for message in messages:
            data = message.as_dict() if hasattr(message, "as_dict") else message
            if data.get("role") == "user":
                user = str(data.get("content", ""))
        return CompletionResult(content=user, model="fake")


def test_parallel_review_survives_a_failing_reviewer() -> None:
    good = RoleAgent(Role("good", "good"), RoleBackend())
    bad = RoleAgent(Role("bad", "bad"), _BoomBackend())
    results = parallel_review([good, bad], "subject")  # must NOT raise
    by_sender = {m.sender: m.content for m in results}
    assert by_sender["good"] == "good"  # the healthy reviewer's work is preserved
    assert by_sender["bad"].startswith("[error]")  # the failure is marked, not fatal


def test_supervisor_sees_raw_reviews_preserving_consensus_strength() -> None:
    agree = "answer is 42"
    workers = [RoleAgent(Role(f"w{i}", agree), RoleBackend()) for i in range(3)]
    workers.append(RoleAgent(Role("w3", "answer is 7"), RoleBackend()))  # lone dissenter
    crew = SupervisorCrew(RoleAgent(Role("boss", "boss"), _EchoUserBackend()), workers)
    result = crew.run("what is the answer")
    # The supervisor's context (echoed into its answer) keeps all 3 agreeing voices — a 3-to-1
    # majority, not the 1-to-1 tie that pre-supervisor consolidation would have produced.
    assert result.answer.count("answer is 42") == 3
    assert "answer is 7" in result.answer


def test_restrict_tools_enforces_allowlist_fail_closed() -> None:
    from chimera.orchestration.roles import _restrict_tools
    from chimera.tools import ToolRegistry
    from chimera.tools.builtin import EchoTool

    reg = ToolRegistry()
    reg.register(EchoTool())
    assert _restrict_tools(reg, ["echo"]).names() == ["echo"]
    assert _restrict_tools(reg, ["nonexistent"]).names() == []  # unknown -> absent, not error
