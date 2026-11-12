"""A Code conversation that sat idle past its cache lifetime is shortened before the next turn.

The cache is gone by then, so the full history would be paid for in full. Only what is SENT is
shortened: the stored conversation, which the screen replays, keeps every exchange.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core import Agent, AgentConfig
from chimera.core.agent import AgentResult
from chimera.core.code_session import (
    CodeSession,
    CodeSessionStore,
    _idle_view_of,
    _last_turn_of,
)
from chimera.core.context_budget import compact, estimate_tokens
from chimera.core.steplog import StepLog, StepRecord
from chimera.interface import ChatSession
from chimera.providers import CompletionResult
from chimera.providers.cache_ttl import ANTHROPIC_EPHEMERAL_TTL_SECONDS
from chimera.tools import ToolRegistry

ANTHROPIC = "anthropic/claude-haiku-5.5"
DEEPSEEK = "deepseek/deepseek-chat"
ENDED = 1_000.0
NOTE = "[earlier conversation, compacted]"
LONG_PAUSE = ENDED + ANTHROPIC_EPHEMERAL_TTL_SECONDS + 60


class _Agent:
    """Records the history it is sent and answers with the transcript a real run returns."""

    def __init__(self) -> None:
        self.sent: list[list[Any]] = []

    def run(self, task: str, *, history: list[Any] | None = None, **_: Any) -> AgentResult:
        self.sent.append(list(history or []))
        transcript = [
            {"role": "system", "content": "sys"},
            *(history or []),
            {"role": "user", "content": task},
            {"role": "assistant", "content": f"re: {task}"},
        ]
        return AgentResult(answer=f"re: {task}", steps=1, stopped_reason="final", transcript=transcript)


def _record(model: str = ANTHROPIC) -> dict[str, Any]:
    return {
        "ended_at": ENDED,
        "model": model,
        "provider": "",
        "system_fingerprint": "",
        "tools_fingerprint": "",
        "prefix_fingerprint": "",
    }


def _conversation(turns: int = 30, size: int = 20_000) -> CodeSession:
    session = CodeSession(_Agent(), session_id="s1")
    body: list[dict[str, Any]] = []
    for i in range(turns):
        body.append({"role": "user", "content": f"pergunta {i} " + "x" * size})
        body.append({"role": "assistant", "content": f"resposta {i} " + "y" * size})
    session.messages = body
    session.last_turn = _record()
    return session


def test_a_long_pause_shortens_what_is_sent_and_nothing_that_is_stored() -> None:
    session = _conversation()
    stored = list(session.messages)

    record = session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)

    assert record is not None
    assert record["gap_seconds"] == 360
    assert record["ttl_seconds"] == ANTHROPIC_EPHEMERAL_TTL_SECONDS
    assert record["estimated"] is True
    assert record["tokens_after"] < record["tokens_before"]
    assert record["saved_tokens"] == record["tokens_before"] - record["tokens_after"]
    sent = session.history_for_model()
    assert len(sent) < len(stored)
    assert str(sent[0]["content"]).startswith(NOTE)
    assert record["tokens_after"] == estimate_tokens(sent)
    assert session.messages == stored


def test_a_pause_within_the_lifetime_leaves_the_history_alone() -> None:
    session = _conversation()
    at_limit = ENDED + ANTHROPIC_EPHEMERAL_TTL_SECONDS
    assert session.compact_if_idle(now=at_limit, enabled=True, model=ANTHROPIC) is None
    assert session.history_for_model() == session.messages


def test_an_unknown_lifetime_never_compacts_however_long_the_pause() -> None:
    session = _conversation()
    session.last_turn = _record(DEEPSEEK)
    assert session.compact_if_idle(now=ENDED + 10_000_000, enabled=True, model=DEEPSEEK) is None
    assert session.idle_view is None


def test_the_switch_off_never_compacts() -> None:
    session = _conversation()
    assert session.compact_if_idle(now=ENDED + 10_000, enabled=False, model=ANTHROPIC) is None
    assert session.idle_view is None


def test_the_first_turn_has_nothing_to_compare_against() -> None:
    session = _conversation()
    session.last_turn = None
    assert session.compact_if_idle(now=ENDED + 10_000, enabled=True, model=ANTHROPIC) is None


def test_an_empty_or_short_history_is_left_as_it_is() -> None:
    session = CodeSession(_Agent(), session_id="s2")
    session.last_turn = _record()
    assert session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC) is None
    session.messages = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "ola"}]
    assert session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC) is None
    assert session.idle_view is None


def test_the_turn_record_and_the_view_survive_the_document_they_are_stored_in() -> None:
    session = _conversation()
    session.last_turn = {**_record(), "provider": "R", "system_fingerprint": "s"}
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    document = session.to_dict()
    assert _last_turn_of(document["last_turn"]) == session.last_turn
    assert _idle_view_of(document["idle_view"]) == session.idle_view
    assert _last_turn_of(None) is None
    assert _last_turn_of({"model": "no time"}) is None
    assert _idle_view_of({"covers": 0, "lead": [{"role": "user"}]}) is None
    assert _idle_view_of({"covers": 3, "lead": []}) is None
    fresh = CodeSession(_Agent(), session_id="s3").to_dict()
    assert "last_turn" not in fresh and "idle_view" not in fresh


def test_the_model_is_sent_the_short_history_and_the_file_keeps_every_exchange(tmp_path: Path) -> None:
    session = _conversation(turns=8, size=50)
    store = CodeSessionStore(tmp_path / "sessions")
    agent = session.agent
    assert isinstance(agent, _Agent)

    assert session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC) is not None
    session.send("pergunta nova")
    store.save(session)

    # The model got the compacted history ...
    assert str(agent.sent[0][0]["content"]).startswith(NOTE)
    assert len(agent.sent[0]) < 16
    # ... and the file holds all eight old exchanges plus the new one, in order.
    data = json.loads((tmp_path / "sessions" / "s1.json").read_text(encoding="utf-8"))
    from chimera.api.code_replay import exchanges_from_messages

    questions = [e["you"] for e in exchanges_from_messages(data["messages"])]
    assert [q.split(" ")[1] for q in questions[:8]] == [str(i) for i in range(8)]
    assert questions[-1] == "pergunta nova"
    assert len(questions) == 9
    assert not any(NOTE in str(m.get("content")) for m in data["messages"])


def test_the_next_turn_inside_the_lifetime_sends_the_same_short_prefix(tmp_path: Path) -> None:
    session = _conversation(turns=8, size=50)
    store = CodeSessionStore(tmp_path / "sessions")
    agent = session.agent
    assert isinstance(agent, _Agent)
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    session.send("um")
    store.save(session)

    reloaded = store.load("s1", agent)
    assert reloaded.idle_view == session.idle_view
    reloaded.send("dois")

    first, second = agent.sent
    assert second[: len(first)] == first
    assert len(reloaded.messages) == 16 + 4


def test_a_run_that_rewrote_the_start_still_appends_only_its_own_turn() -> None:
    session = _conversation(turns=8, size=50)
    stored = list(session.messages)
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    sent = session.history_for_model()

    # The agent compacted again inside the run, so its transcript does not start from what it was sent.
    rewritten = [
        {"role": "user", "content": f"{NOTE}\nresumo"},
        {"role": "user", "content": "tarefa"},
        {"role": "assistant", "content": "feito"},
    ]
    result = AgentResult(answer="feito", steps=1, stopped_reason="final", transcript=rewritten)
    session.absorb(result, sent=sent, task="tarefa")

    assert session.messages == [*stored, rewritten[1], rewritten[2]]


def test_trimming_the_stored_messages_moves_what_the_note_stands_for() -> None:
    # 16 stored messages, the note stands for the first 10 (all but the 6 kept). The turn adds 2,
    # and a limit of 14 drops the 4 oldest: the note now stands for the 6 that are left of its span.
    session = _conversation(turns=8, size=50)
    session.max_messages = 14
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    assert session.idle_view is not None and session.idle_view["covers"] == 10

    session.send("nova")

    assert len(session.messages) == 14
    assert session.idle_view is not None and session.idle_view["covers"] == 6


def test_a_trim_that_takes_everything_the_note_stood_for_drops_the_note() -> None:
    session = _conversation(turns=8, size=50)
    session.max_messages = 6
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)

    session.send("nova")

    assert len(session.messages) == 6
    assert session.idle_view is None


def test_a_view_that_stands_for_more_than_is_stored_is_dropped() -> None:
    session = _conversation(turns=8, size=50)
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    session.messages = session.messages[:4]  # cut under the note

    assert session.history_for_model() == session.messages
    assert session.idle_view is None


def test_clearing_the_conversation_clears_the_view() -> None:
    session = _conversation(turns=8, size=50)
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    session.clear()
    assert session.idle_view is None and session.messages == []


class _Backend:
    def complete(self, *_a: Any, **_k: Any) -> CompletionResult:  # pragma: no cover - never called
        raise AssertionError("compaction must not call the model")


def _tool_conversation(pairs: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for i in range(pairs):
        messages.append({"role": "user", "content": f"leia o arquivo {i}"})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"conteudo {i}"})
        messages.append({"role": "assistant", "content": f"li {i}"})
    return messages


def _dangling(messages: list[dict[str, Any]]) -> list[str]:
    """Tool results whose assistant tool_call is not before them, and calls nothing answered."""
    seen: set[str] = set()
    answered: set[str] = set()
    problems: list[str] = []
    for m in messages:
        for call in m.get("tool_calls") or []:
            seen.add(call["id"])
        if m.get("role") == "tool":
            answered.add(m["tool_call_id"])
            if m["tool_call_id"] not in seen:
                problems.append(f"orphan result {m['tool_call_id']}")
    problems += [f"unanswered call {c}" for c in sorted(seen - answered)]
    return problems


def test_the_idle_compaction_is_the_agents_own_and_never_cuts_a_tool_pair() -> None:
    agent = Agent(_Backend(), ToolRegistry(), AgentConfig(keep_recent=5))
    agent.run_state.plan = "1. ler tudo\n2. resumir"
    agent.run_state.tasks = ["ler arquivos: em andamento"]
    session = CodeSession(agent, session_id="tools")
    session.messages = _tool_conversation(10)
    session.last_turn = _record()

    record = session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)

    assert record is not None and record["summary_usd"] == 0.0
    sent = session.history_for_model()
    assert _dangling(sent) == []
    # What the run was doing is restored beside the note.
    assert str(sent[0]["content"]).startswith(NOTE)
    restored = " ".join(str(m["content"]) for m in sent[:3])
    assert "ler tudo" in restored and "ler arquivos: em andamento" in restored
    # keep_recent comes from the agent's config: with a larger one the cut moves.
    other = Agent(_Backend(), ToolRegistry(), AgentConfig(keep_recent=9))
    longer, _ = other.compact_history(list(session.messages))
    shorter, _ = agent.compact_history(list(session.messages))
    assert len(longer) > len(shorter)
    assert _dangling(longer) == []


def test_the_agents_pre_compact_hook_runs_for_an_idle_compaction() -> None:
    agent = Agent(_Backend(), ToolRegistry(), AgentConfig())
    calls: list[str] = []

    class _Lifecycle:
        def pre_compact(self, trigger: str) -> None:
            calls.append(trigger)

    agent.tools.lifecycle = _Lifecycle()  # type: ignore[attr-defined]
    agent.compact_history(_tool_conversation(10))
    assert calls == ["auto"]


# --- the whole path, through the route the screen uses ---------------------------------------


class _RouteAgent(_Agent):
    config = type("C", (), {"model": ANTHROPIC, "max_usd": None, "warn_usd": None})()
    sent_log: list[list[Any]] = []
    last_compaction_usd = 0.0

    summary_cost = 0.0
    spends: list[Any] = []
    explode = False

    def compact_history(
        self, messages: list[Any], *, spend: Any = None, summarise: bool = True
    ) -> tuple[list[Any], bool]:
        if _RouteAgent.explode:
            raise RuntimeError("the compactor broke")
        _RouteAgent.spends.append(spend)
        if _RouteAgent.summary_cost and spend is not None:
            spend.charge(_RouteAgent.summary_cost, label="summary")
        _RouteAgent.last_compaction_usd = _RouteAgent.summary_cost
        return compact(messages)

    def run(self, task: str, *, history: list[Any] | None = None, **kw: Any) -> AgentResult:
        _RouteAgent.sent_log.append(list(history or []))
        result = super().run(task, history=history, **kw)
        result.usd = 0.01
        log = StepLog()
        log.add(
            StepRecord(
                index=0,
                prompt_tokens=100,
                completion_tokens=10,
                model=ANTHROPIC,
                cached_tokens=0,
                provider="Anthropic",
                system_fingerprint="s",
                tools_fingerprint="t",
                prefix_fingerprint="p",
            )
        )
        result.steplog = log
        result.model = ANTHROPIC
        return result


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    _RouteAgent.sent_log = []
    _RouteAgent.spends = []
    _RouteAgent.summary_cost = 0.0
    _RouteAgent.explode = False
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _RouteAgent(), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(_RouteAgent()), workspace=ws, settings=settings))


def _done(response: Any) -> dict[str, Any]:
    event, out = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out["done"]


def test_after_a_long_pause_the_screen_still_shows_every_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    for i in range(6):
        _done(client.post("/api/code/turn", json={"message": f"pergunta {i}", "session_id": "idle1"}))

    # Make the pause longer than the cache lives, by moving the end of the last turn back in time.
    (path,) = list((tmp_path / "home").rglob("idle1.json"))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["last_turn"]["ended_at"] = 1.0
    path.write_text(json.dumps(document), encoding="utf-8")

    done = _done(client.post("/api/code/turn", json={"message": "depois da pausa", "session_id": "idle1"}))

    assert done["idle_compaction"] is not None
    assert done["idle_compaction"]["estimated"] is True
    # The model was sent the shortened history ...
    sent = _RouteAgent.sent_log[-1]
    assert str(sent[0]["content"]).startswith(NOTE)
    assert len(sent) < 12
    # ... and the conversation the owner sees has every exchange, in order, with no note in it.
    view = client.get("/api/code/sessions/idle1").json()
    questions = [e["you"] for e in view["exchanges"]]
    assert questions == [f"pergunta {i}" for i in range(6)] + ["depois da pausa"]
    assert NOTE not in json.dumps(view["exchanges"])

    # A turn inside the lifetime sends the same shortened prefix, and does not compact again.
    again = _done(client.post("/api/code/turn", json={"message": "logo depois", "session_id": "idle1"}))
    assert again["idle_compaction"] is None
    first, second = _RouteAgent.sent_log[-2:]
    assert second[: len(first)] == first
    view = client.get("/api/code/sessions/idle1").json()
    assert len(view["exchanges"]) == 8


def test_refresh_loads_the_turn_record_and_the_view_from_the_stored_conversation(tmp_path: Path) -> None:
    session = _conversation(turns=8, size=50)
    store = CodeSessionStore(tmp_path / "sessions")
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    store.save(session)

    stale = CodeSession(session.agent, session_id="s1")
    assert stale.last_turn is None and stale.idle_view is None
    store.refresh(stale)
    assert stale.last_turn == session.last_turn
    assert stale.idle_view == session.idle_view


def test_a_conversation_stored_before_the_record_has_no_turn_record_and_compacts_nothing(
    tmp_path: Path,
) -> None:
    session = _conversation(turns=8, size=50)
    session.last_turn = None
    store = CodeSessionStore(tmp_path / "sessions")
    store.save(session)

    loaded = store.load("s1", session.agent)
    assert loaded.last_turn is None and loaded.idle_view is None
    assert loaded.messages  # the history is there, and nothing says when the last turn ended
    assert loaded.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC) is None


def _idle_route_conversation(client: TestClient, tmp_path: Path) -> None:
    for i in range(6):
        _done(client.post("/api/code/turn", json={"message": f"pergunta {i}", "session_id": "idle1"}))
    (path,) = list((tmp_path / "home").rglob("idle1.json"))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["last_turn"]["ended_at"] = 1.0
    path.write_text(json.dumps(document), encoding="utf-8")


def test_what_the_summariser_charged_is_in_the_receipt_and_on_the_turns_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _idle_route_conversation(client, tmp_path)
    _RouteAgent.summary_cost = 0.05
    _RouteAgent.spends = []

    done = _done(client.post("/api/code/turn", json={"message": "depois", "session_id": "idle1"}))

    assert done["idle_compaction"]["summary_usd"] == 0.05
    assert done["usd"] == 0.06  # the run's 0.01 and the summary's 0.05
    # The meter the summariser was charged to is the turn's own, the one the run is held to.
    (spend,) = _RouteAgent.spends
    assert spend is not None and spend.spent == 0.05


def test_an_unpriced_summary_makes_the_turns_cost_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _idle_route_conversation(client, tmp_path)
    monkeypatch.setattr(_RouteAgent, "last_compaction_usd", None, raising=False)
    original = _RouteAgent.compact_history

    def unpriced(self: Any, messages: list[Any], **kw: Any) -> tuple[list[Any], bool]:
        out = original(self, messages, **kw)
        _RouteAgent.last_compaction_usd = None  # type: ignore[assignment]
        return out

    monkeypatch.setattr(_RouteAgent, "compact_history", unpriced)
    done = _done(client.post("/api/code/turn", json={"message": "depois", "session_id": "idle1"}))
    assert done["idle_compaction"]["summary_usd"] is None
    assert done["usd"] is None


def test_a_failing_compaction_does_not_fail_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _idle_route_conversation(client, tmp_path)
    _RouteAgent.explode = True

    done = _done(client.post("/api/code/turn", json={"message": "depois", "session_id": "idle1"}))

    assert done["idle_compaction"] is None
    assert len(_RouteAgent.sent_log[-1]) == 12  # sent as stored
    view = client.get("/api/code/sessions/idle1").json()
    assert len(view["exchanges"]) == 7


class _PricedBackend:
    """A backend whose summary call is billed: 100k tokens in, 1k out on a priced model."""

    def __init__(self) -> None:
        self.prompts: list[Any] = []

    def complete(self, prompt: Any, **_: Any) -> CompletionResult:
        self.prompts.append(prompt)
        return CompletionResult(
            content="Always answer in Portuguese.",
            model=ANTHROPIC,
            prompt_tokens=100_000,
            completion_tokens=1_000,
        )


def _agent_with_summariser(backend: _PricedBackend) -> Agent:
    return Agent(backend, ToolRegistry(), AgentConfig(summarise_compaction=True, model=ANTHROPIC))


def test_a_summariser_call_is_charged_to_the_turns_ceiling() -> None:
    from chimera.orchestration.budget import SpendBudget

    backend = _PricedBackend()
    session = CodeSession(_agent_with_summariser(backend), session_id="priced")
    session.messages = _tool_conversation(10)
    session.last_turn = _record()
    spend = SpendBudget(max_usd=0.05, strict=False)

    record = session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC, spend=spend)

    assert record is not None
    assert len(backend.prompts) == 1
    assert record["summary_usd"] == 0.084
    assert spend.spent == 0.084
    # 0.084 is over the 0.05 ceiling, so the run that follows is refused rather than let past it.
    assert spend.blocked() is not None
    assert "Always answer in Portuguese." in str(session.history_for_model()[0]["content"])


def test_a_strict_ceiling_that_cannot_afford_the_summary_gets_the_note_instead() -> None:
    from chimera.orchestration.budget import SpendBudget

    backend = _PricedBackend()
    session = CodeSession(_agent_with_summariser(backend), session_id="strict")
    session.messages = _tool_conversation(10)
    session.last_turn = _record()
    spend = SpendBudget(max_usd=0.0001, strict=True)

    record = session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC, spend=spend)

    assert record is not None
    assert backend.prompts == []
    assert spend.spent == 0.0
    assert "Standing from that span" not in str(session.history_for_model()[0]["content"])


def test_a_conversation_with_a_tainted_turn_is_never_summarised_by_a_model() -> None:
    backend = _PricedBackend()
    session = CodeSession(_agent_with_summariser(backend), session_id="tainted")
    session.messages = _tool_conversation(10)
    session.last_turn = _record()
    session.receipts = [{"tainted": False}, {"tainted": True}]

    record = session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)

    assert record is not None and record["summary_usd"] == 0.0
    assert backend.prompts == []  # nothing the tainted turns read was sent to a model
    note = str(session.history_for_model()[0]["content"])
    assert note.startswith(NOTE) and "Standing from that span" not in note

    # The same conversation, untainted, is summarised.
    session.idle_view = None
    session.receipts = [{"tainted": False}]
    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)
    assert len(backend.prompts) == 1


def test_the_stored_note_carries_no_request_from_the_turn_before() -> None:
    agent = Agent(_Backend(), ToolRegistry(), AgentConfig(keep_recent=4))
    agent.run_state.task = "tarefa antiga"
    agent.run_state.latest_request = "pedido antigo"
    agent.run_state.plan = "1. continuar"
    session = CodeSession(agent, session_id="stale")
    session.messages = _tool_conversation(10)
    session.last_turn = _record()

    session.compact_if_idle(now=LONG_PAUSE, enabled=True, model=ANTHROPIC)

    assert session.idle_view is not None
    lead = json.dumps(session.idle_view["lead"])
    assert "pedido antigo" not in lead and "tarefa antiga" not in lead
    assert "continuar" in lead  # the state that is still true is kept
    # The agent's own state was not altered to get there.
    assert agent.run_state.latest_request == "pedido antigo"


def test_the_pre_compact_hook_is_not_run_when_nothing_was_compacted() -> None:
    agent = Agent(_Backend(), ToolRegistry(), AgentConfig())
    calls: list[str] = []

    class _Lifecycle:
        def pre_compact(self, trigger: str) -> None:
            calls.append(trigger)

    agent.tools.lifecycle = _Lifecycle()  # type: ignore[attr-defined]
    _, changed = agent.compact_history(_tool_conversation(1))
    assert changed is False and calls == []
