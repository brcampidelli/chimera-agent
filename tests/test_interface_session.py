"""Tests for the conversational session core (no network)."""

from __future__ import annotations

from pathlib import Path

from chimera.core.agent import AgentResult
from chimera.interface import ChatSession
from chimera.memory import MemoryManager, MemoryStore


class RecordingAgent:
    """A fake agent that records the prompt it received and echoes a reply."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, task: str) -> AgentResult:
        self.prompts.append(task)
        return AgentResult(answer=f"reply#{len(self.prompts)}", steps=1, stopped_reason="final")


def test_send_records_turns_and_returns_answer() -> None:
    agent = RecordingAgent()
    session = ChatSession(agent)
    assert session.send("hello") == "reply#1"
    assert len(session.turns) == 1
    assert session.turns[0].user == "hello"
    assert session.turns[0].assistant == "reply#1"


def test_history_is_threaded_into_later_prompts() -> None:
    agent = RecordingAgent()
    session = ChatSession(agent)
    session.send("my name is Bruno")
    session.send("what is my name?")
    assert "my name is Bruno" in agent.prompts[1]
    assert "reply#1" in agent.prompts[1]


def test_reset_clears_conversation() -> None:
    agent = RecordingAgent()
    session = ChatSession(agent)
    session.send("a")
    session.reset()
    assert session.turns == []
    session.send("b")
    assert "Conversation so far" not in agent.prompts[-1]


def test_memory_is_recalled_into_the_prompt(tmp_path: Path) -> None:
    memory = MemoryManager(MemoryStore(tmp_path / "m.json"))
    memory.remember("Bruno prefers absolute imports")
    agent = RecordingAgent()
    session = ChatSession(agent, memory=memory)
    session.send("any rule about imports?")
    assert "absolute imports" in agent.prompts[0]


def test_history_window_is_bounded() -> None:
    agent = RecordingAgent()
    session = ChatSession(agent, max_history=2)
    for i in range(5):
        session.send(f"msg{i}")
    last_prompt = agent.prompts[-1]  # the prompt for "msg4"
    assert "msg3" in last_prompt and "msg2" in last_prompt  # within the 2-turn window
    assert "msg1" not in last_prompt and "msg0" not in last_prompt  # outside it
