"""A coding conversation keeps what it DID, not a summary of what it said.

The failure this exists to prevent is quiet: with the conversation flattened to prose, turn two
arrives at an agent with no record of the files turn one read, so "now fix the other one" makes it
read everything again. Nothing errors. It is just slower, more expensive, and worse — which is why
the first test asserts on the messages the agent was handed rather than on its answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.core.agent import AgentResult
from chimera.core.code_session import (
    CodeSession,
    CodeSessionStore,
    trim_to_a_safe_boundary,
)


class _RecordingAgent:
    """An agent that records the history it was handed and returns a growing transcript."""

    def __init__(self) -> None:
        self.histories: list[list[Any]] = []
        self.turn = 0

    def run(self, task: str, *, history: list[Any] | None = None, **_: Any) -> AgentResult:
        self.histories.append(list(history or []))
        self.turn += 1
        transcript: list[Any] = [{"role": "system", "content": "SYSTEM"}]
        transcript += list(history or [])
        transcript += [
            {"role": "user", "content": task},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{self.turn}"}]},
            {"role": "tool", "tool_call_id": f"c{self.turn}", "content": f"read file {self.turn}"},
            {"role": "assistant", "content": f"answer {self.turn}"},
        ]
        return AgentResult(
            answer=f"answer {self.turn}", steps=1, stopped_reason="final", transcript=transcript
        )


def test_the_second_turn_sees_the_first_turns_tool_calls() -> None:
    agent = _RecordingAgent()
    session = CodeSession(agent)

    session.send("read a.py")
    session.send("now fix the other one")

    first, second = agent.histories
    assert first == []  # the first turn has nothing to carry
    roles = [m["role"] for m in second]
    assert "tool" in roles, "the tool result was dropped — the agent re-explores every turn"
    assert any(m.get("content") == "read file 1" for m in second)


def test_the_system_message_is_never_carried_forward() -> None:
    """Rebuilt every turn on purpose: retrieved skills follow the current task and project
    instructions follow the file in focus, so a stored one pins both to turn one's subject."""
    agent = _RecordingAgent()
    session = CodeSession(agent)
    session.send("first")
    session.send("second")

    assert all(m["role"] != "system" for m in session.messages)
    assert all(m["role"] != "system" for m in agent.histories[1])


def test_the_conversation_is_replaced_not_appended() -> None:
    """The transcript already CONTAINS the history it was handed. Appending it duplicates the whole
    conversation every turn — invisible until the prompt is four times the expected size."""
    agent = _RecordingAgent()
    session = CodeSession(agent)
    session.send("one")
    after_one = len(session.messages)
    session.send("two")

    assert len(session.messages) == after_one * 2  # one turn's worth added, not the whole thing
    assert [m["content"] for m in session.messages].count("read file 1") == 1


def test_an_empty_transcript_leaves_the_conversation_alone() -> None:
    """A stub backend, or a failure before the first model call, must not erase the session."""

    class _Silent:
        def run(self, task: str, **_: Any) -> AgentResult:
            return AgentResult(answer="", steps=0, stopped_reason="final")

    session = CodeSession(_Silent(), messages=[{"role": "user", "content": "keep me"}])
    session.send("anything")
    assert session.messages == [{"role": "user", "content": "keep me"}]


def test_trimming_never_orphans_a_tool_result() -> None:
    """An assistant message with `tool_calls` and the `tool` messages answering it are one unit. A
    `tool` message whose call is gone is a hard provider error, not a degradation — so the cut can
    only ever land on a `user` message, which is where every turn starts."""
    messages: list[Any] = []
    for i in range(10):
        messages += [
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": f"o{i}"},
            {"role": "assistant", "content": f"a{i}"},
        ]

    kept = trim_to_a_safe_boundary(messages, 10)
    assert kept[0]["role"] == "user"
    open_calls = {c["id"] for m in kept for c in m.get("tool_calls", [])}
    answered = {m["tool_call_id"] for m in kept if m["role"] == "tool"}
    assert answered <= open_calls, f"orphaned tool results: {answered - open_calls}"


def test_trimming_keeps_slightly_more_rather_than_slightly_less() -> None:
    """Scanning FORWARD to the next boundary overshoots the limit. That is the deliberate
    direction: too big fails as a larger prompt, too small fails as a 400."""
    messages: list[Any] = [{"role": "user", "content": "q"}] + [
        {"role": "assistant", "content": f"a{i}"} for i in range(9)
    ]
    assert len(trim_to_a_safe_boundary(messages * 2, 5)) >= 5


def test_a_conversation_with_no_boundary_at_all_is_dropped() -> None:
    """Nothing here is a safe place to resume from, and inventing one would mean handing a model a
    tool result for a call it never made."""
    assert trim_to_a_safe_boundary([{"role": "assistant", "content": "x"}] * 5, 2) == []


def test_a_short_conversation_is_untouched() -> None:
    messages: list[Any] = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    assert trim_to_a_safe_boundary(messages, 10) is messages


def test_the_store_round_trips_a_conversation(tmp_path: Path) -> None:
    agent = _RecordingAgent()
    store = CodeSessionStore(tmp_path)
    session = CodeSession(agent, session_id="abc")
    session.send("do a thing")
    store.save(session)

    restored = store.load("abc", agent)
    assert restored.session_id == "abc"
    assert restored.messages == session.messages
    assert store.list_ids() == ["abc"]


def test_an_unknown_session_is_a_fresh_one_not_an_error(tmp_path: Path) -> None:
    """A missing file is the ordinary first-turn case."""
    session = CodeSessionStore(tmp_path).load("brand-new", _RecordingAgent())
    assert session.session_id == "brand-new" and session.messages == []


def test_a_corrupt_session_file_starts_fresh_instead_of_failing(tmp_path: Path) -> None:
    """Refusing to start a conversation because an old one is unreadable turns one bad write into a
    permanently broken session."""
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    session = CodeSessionStore(tmp_path).load("broken", _RecordingAgent())
    assert session.messages == []


def test_a_session_id_cannot_escape_its_directory(tmp_path: Path) -> None:
    """The id is generated here, but it is also reachable from an API parameter."""
    store = CodeSessionStore(tmp_path / "sessions")
    session = CodeSession(_RecordingAgent(), session_id="../../escaped")
    store.save(session)

    assert not (tmp_path / "escaped.json").exists()
    assert [p.name for p in (tmp_path / "sessions").iterdir()] == ["escaped.json"]

    with pytest.raises(ValueError):
        store.save(CodeSession(_RecordingAgent(), session_id="../.."))


def test_stored_sessions_are_plain_json(tmp_path: Path) -> None:
    """Readable by a human debugging a session, and by anything that is not this class."""
    store = CodeSessionStore(tmp_path)
    session = CodeSession(_RecordingAgent(), session_id="s1")
    session.send("hello")
    store.save(session)

    data = json.loads((tmp_path / "s1.json").read_text(encoding="utf-8"))
    assert data["session_id"] == "s1"
    assert data["messages"][0]["role"] == "user"


def test_the_agent_splices_history_between_system_and_task() -> None:
    """Asserted on the real Agent, not the stub: order is the contract the provider enforces."""
    from chimera.core.agent import Agent, AgentConfig
    from chimera.providers import CompletionResult
    from chimera.tools import ToolRegistry

    class _Backend:
        def __init__(self) -> None:
            self.seen: list[Any] = []

        def complete(self, messages: list[Any], **_: object) -> CompletionResult:
            self.seen = list(messages)
            return CompletionResult(content="ok", model="test")

    backend = _Backend()
    agent = Agent(backend, ToolRegistry(), AgentConfig())
    agent.run("new task", history=[{"role": "user", "content": "old"},
                                   {"role": "assistant", "content": "older answer"}])

    roles = [m["role"] for m in backend.seen]
    assert roles == ["system", "user", "assistant", "user"]
    assert backend.seen[-1]["content"] == "new task"


def test_history_is_optional_and_changes_nothing_when_absent() -> None:
    from chimera.core.agent import Agent, AgentConfig
    from chimera.providers import CompletionResult
    from chimera.tools import ToolRegistry

    class _Backend:
        def __init__(self) -> None:
            self.seen: list[Any] = []

        def complete(self, messages: list[Any], **_: object) -> CompletionResult:
            self.seen = list(messages)
            return CompletionResult(content="ok", model="test")

    backend = _Backend()
    Agent(backend, ToolRegistry(), AgentConfig()).run("just this")
    assert [m["role"] for m in backend.seen] == ["system", "user"]
