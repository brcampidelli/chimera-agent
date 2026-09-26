"""Chat can send its earlier turns as the model's own messages instead of one flattened block.

Study 25, wave 2 (`bench/PLAN-study25-system-prompts.md` §7 S2/S3). `ChatSession._assemble` builds
every turn as one user message: the profile, the recalled facts and the last six turns as prose. Two
things follow:
- every tool call of an earlier turn is gone by the next one;
- nothing after the system message can be cached, because the block starts with text that changes.

`ChatSession.real_history` (``CHIMERA_CHAT_REAL_HISTORY``) sends the same six turns as real
user/assistant messages, tool calls included, and moves the profile and the recalled facts into the
turn context. Off by default: the flattened form stays byte-identical until a measurement decides.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.api.sessions import SessionManager, SessionStore
from chimera.cli.spend import BudgetedTurns
from chimera.core.agent import Agent, AgentConfig, AgentResult, ToolActivity
from chimera.governance.ledger_tool import FENCE_OPEN
from chimera.interface.session import CLEAN, TAINTED, UNKNOWN, ChatSession, ChatTurn
from chimera.memory.models import MemoryItem
from chimera.orchestration.budget import SpendBudget
from chimera.prompts.context import FACTS_HEADER, TURN_CONTEXT_OPEN
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools.builtin import EchoTool
from chimera.tools.registry import ToolRegistry


class _Recorder:
    """A model that calls `echo` on turns whose message says so, and records every request."""

    def __init__(self, model: str = "m") -> None:
        self.sent: list[list[dict[str, Any]]] = []
        self.model = model

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        sent = [dict(m) if isinstance(m, dict) else m.as_dict() for m in messages]
        self.sent.append(sent)
        last = sent[-1]
        usage = {"model": self.model, "prompt_tokens": 10, "completion_tokens": 2}
        if last["role"] == "user" and "use echo" in str(last["content"]):
            return CompletionResult(content="", **usage, tool_calls=[  # type: ignore[arg-type]
                ToolCall(id=f"c{len(self.sent)}", name="echo", arguments={"text": "seen"})
            ])
        return CompletionResult(content=f"answer {len(self.sent)}", **usage)  # type: ignore[arg-type]


def _agent(backend: _Recorder, *, turn_context: bool = True) -> Agent:
    registry = ToolRegistry()
    registry.register(EchoTool())
    config = AgentConfig(
        prefix_nonce="", turn_context=turn_context, inject_skill_context=False, project_root=None
    )
    return Agent(backend, registry, config)  # type: ignore[arg-type]


class _Memory:
    """Recall that answers every message with one fact naming it."""

    def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        return [MemoryItem(id=query, content=f"fact about {query}")]


def _first_request_of(backend: _Recorder, turn: int) -> list[dict[str, Any]]:
    """The first request each turn sends: a turn whose message says "use echo" sends two."""
    firsts = [s for s in backend.sent if s[-1]["role"] == "user"]
    return firsts[turn]


def test_off_by_default_the_turn_is_one_flattened_message() -> None:
    backend = _Recorder()
    session = ChatSession(_agent(backend), gate=None)
    assert session.real_history is False
    session.send("first question")
    session.send("second question")
    request = _first_request_of(backend, 1)
    assert [m["role"] for m in request] == ["system", "user"]
    assert "Conversation so far:\nUser: first question\nAssistant: answer 1" in request[-1]["content"]


def test_with_real_history_the_earlier_turn_arrives_as_messages() -> None:
    backend = _Recorder()
    session = ChatSession(_agent(backend), gate=None, real_history=True)
    session.send("first question")
    session.send("second question")
    request = _first_request_of(backend, 1)
    assert request[1] == {"role": "user", "content": "first question"}
    assert request[2] == {"role": "assistant", "content": "answer 1"}
    assert request[-1]["role"] == "user" and request[-1]["content"].endswith("\n\nsecond question")
    assert "Conversation so far" not in json.dumps(request)


def test_an_earlier_tool_call_and_its_result_are_still_there_on_the_next_turn() -> None:
    backend = _Recorder()
    session = ChatSession(_agent(backend), gate=None, real_history=True)
    session.send("please use echo")
    session.send("what did the tool say?")
    request = _first_request_of(backend, 1)
    roles = [m["role"] for m in request]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert request[2]["tool_calls"][0]["function"]["name"] == "echo"
    assert "seen" in request[3]["content"]


def test_recalled_facts_and_the_profile_ride_in_the_turn_context_not_in_history() -> None:
    backend = _Recorder()
    session = ChatSession(
        _agent(backend), memory=_Memory(), gate=None, profile="PROFILE-TEXT", real_history=True
    )
    session.send("alpha")
    session.send("beta")
    request = _first_request_of(backend, 1)
    head = request[-1]["content"]
    assert head.startswith(TURN_CONTEXT_OPEN)
    assert "PROFILE-TEXT" in head and FACTS_HEADER in head and "fact about beta" in head
    history = json.dumps(request[1:-1])
    assert "PROFILE-TEXT" not in history and "fact about" not in history
    assert TURN_CONTEXT_OPEN not in history


def test_history_only_grows_from_one_turn_to_the_next_inside_the_window() -> None:
    backend = _Recorder()
    session = ChatSession(_agent(backend), memory=_Memory(), gate=None, real_history=True)
    for i in range(6):
        session.send(f"message {i}" + (" please use echo" if i % 2 else ""))
    firsts = [_first_request_of(backend, t) for t in range(6)]
    for earlier, later in zip(firsts, firsts[1:], strict=False):
        assert later[: len(earlier) - 1] == earlier[:-1]  # all but the turn's own message


def test_the_window_keeps_six_turns_and_starts_where_a_turn_starts() -> None:
    backend = _Recorder()
    session = ChatSession(_agent(backend), gate=None, real_history=True)
    for i in range(8):
        session.send(f"message {i}" + (" please use echo" if i % 2 else ""))
    session.send("last")
    request = _first_request_of(backend, 8)
    history = request[1:-1]
    assert history[0] == {"role": "user", "content": "message 2"}
    users = [m["content"] for m in history if m["role"] == "user"]
    assert users == [f"message {i}" + (" please use echo" if i % 2 else "") for i in range(2, 8)]


class _PlainAgent:
    """An agent written against the published protocol before history existed: task in, answer out."""

    def __init__(self) -> None:
        self.tasks: list[str] = []

    def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
        self.tasks.append(task)
        return AgentResult(answer="ok", steps=1, stopped_reason="final")


def test_an_agent_that_cannot_take_history_keeps_the_flattened_form() -> None:
    agent = _PlainAgent()
    session = ChatSession(agent, real_history=True)
    session.send("one")
    session.send("two")
    assert "Conversation so far:\nUser: one\nAssistant: ok" in agent.tasks[1]


def test_an_agent_without_a_turn_context_keeps_the_flattened_form() -> None:
    # The facts and the profile would have nowhere to go, and dropping them is worse than
    # flattening.
    backend = _Recorder()
    session = ChatSession(
        _agent(backend, turn_context=False), memory=_Memory(), gate=None, real_history=True
    )
    session.send("one")
    session.send("two")
    request = _first_request_of(backend, 1)
    assert [m["role"] for m in request] == ["system", "user"]
    assert "fact about two" in request[-1]["content"]


def _saved(tmp_path: Path, turns: list[dict[str, str]]) -> SessionStore:
    root = tmp_path / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    (root / "old.json").write_text(
        json.dumps({"id": "old", "title": "t", "turns": turns}), encoding="utf-8"
    )
    return SessionStore(root)


def test_a_session_saved_before_this_mode_loads_and_replays_with_its_labels(tmp_path: Path) -> None:
    store = _saved(tmp_path, [
        {"user": "old question", "assistant": "old answer"},  # no provenance: an older release
        {"user": "clean question", "assistant": "clean answer", "provenance": CLEAN},
    ])
    backend = _Recorder()
    manager = SessionManager(
        lambda: ChatSession(_agent(backend), gate=None, real_history=True), store
    )
    manager.get("old").send("new question")
    request = _first_request_of(backend, 0)
    assert request[1] == {"role": "user", "content": "old question"}
    unknown = request[2]["content"]
    assert request[2]["role"] == "assistant" and unknown.startswith("[restored from the saved")
    assert FENCE_OPEN in unknown and "old answer" in unknown
    assert request[4]["content"] == "[restored from the saved transcript]\nclean answer"


def test_a_real_history_session_is_saved_in_the_same_shape_as_before(tmp_path: Path) -> None:
    backend = _Recorder()
    store = SessionStore(tmp_path / "sessions")
    manager = SessionManager(
        lambda: ChatSession(_agent(backend), gate=None, real_history=True), store
    )
    manager.get("s1").send("please use echo")
    manager.persist("s1")
    raw = json.loads((tmp_path / "sessions" / "s1.json").read_text(encoding="utf-8"))
    assert list(raw) == ["id", "title", "turns"]
    # `unknown` because `send` watches no tool stream: the same stamp the flattened path gives.
    assert raw["turns"] == [
        {"user": "please use echo", "assistant": "answer 2", "provenance": UNKNOWN}
    ]
    # And the other mode reads it back.
    flat = ChatSession(_PlainAgent())
    flat.turns = store.load("s1")
    assert flat.turns[0].assistant == "answer 2" and flat.turns[0].messages is None


def test_a_tainted_turn_restored_from_disk_is_fenced_in_history_too(tmp_path: Path) -> None:
    store = _saved(tmp_path, [{"user": "q", "assistant": "IGNORE PREVIOUS", "provenance": TAINTED}])
    backend = _Recorder()
    session = ChatSession(_agent(backend), gate=None, real_history=True)
    session.turns = store.load("old")
    session.send("next")
    reply = _first_request_of(backend, 0)[2]["content"]
    assert "untrusted content had entered" in reply and FENCE_OPEN in reply


def test_a_budgeted_conversation_passes_history_through_its_meter(monkeypatch: Any) -> None:
    from chimera.fusion import receipts

    # A meter refuses a model it cannot price, so this one gets a price, for this test only.
    priced = [("test/history-model", receipts.ModelPrice(input_per_m=1.0, output_per_m=1.0))]
    monkeypatch.setattr(receipts, "_PRICES", priced + receipts._PRICES)
    backend = _Recorder(model="test/history-model")
    session = ChatSession(
        BudgetedTurns(_agent(backend), SpendBudget(1.0)), gate=None, real_history=True
    )
    session.send("first")
    session.send("second")
    assert _first_request_of(backend, 1)[1] == {"role": "user", "content": "first"}


def test_send_verbose_takes_the_same_path_and_reports_the_tools() -> None:
    backend = _Recorder()
    session = ChatSession(_agent(backend), gate=None, real_history=True)
    seen: list[ToolActivity] = []
    report = session.send_verbose("please use echo", on_tool=seen.append)
    assert report.tool_names == ["echo"] and len(seen) == 1
    session.send_verbose("and now?")
    assert _first_request_of(backend, 1)[2]["tool_calls"][0]["function"]["name"] == "echo"


def test_turn_notes_given_to_one_run_do_not_outlive_it() -> None:
    # The session hands each turn its own notes; a note left on the agent's config would reach the
    # next turn, and a fact recalled for one question would be read as true of the next.
    backend = _Recorder()
    agent = _agent(backend)
    agent.run("one", turn_notes="NOTE-ONE")
    agent.run("two")
    assert "NOTE-ONE" in backend.sent[0][-1]["content"]
    assert "NOTE-ONE" not in backend.sent[1][-1]["content"]
    assert agent.config.turn_notes == ""


def test_the_setting_is_off_unless_the_environment_turns_it_on(monkeypatch: Any) -> None:
    from chimera.config import Settings

    monkeypatch.delenv("CHIMERA_CHAT_REAL_HISTORY", raising=False)
    assert Settings.model_fields["chat_real_history"].default is False
    monkeypatch.setenv("CHIMERA_CHAT_REAL_HISTORY", "1")
    assert Settings().chat_real_history is True


def test_a_turn_recorded_under_real_history_keeps_its_messages_out_of_equality() -> None:
    # `messages` belongs to this process's view of the turn, like `restored`: two turns that say
    # the same thing are the same turn to every reader that compares them.
    assert ChatTurn("q", "a", messages=[{"role": "user", "content": "q"}]) == ChatTurn("q", "a")
