"""A running coding turn can be steered: what the owner types reaches the agent between two steps.

Study 30, item S30-66. A turn that set off in the wrong direction could only be stopped and started
again, paying again for everything it had already read. Now text sent to a running turn
(`POST /api/code/turns/{id}/guidance`, the bridge's `conversations.guidance`, `chimera sessions
attach`) is queued and read by the agent loop at its next step boundary — never while a tool runs —
as a user message of the conversation, and the turn's receipt lists it as the owner's, untainted.

The tests hold the four claims that make it more than a queue: where it lands in the loop, that it is
on the receipt, that a finished turn refuses it out loud, and that one turn with a correction stays
one turn when the conversation is reopened.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.code_replay import attach_receipts, exchanges_from_messages
from chimera.api.live_turns import LiveTurns
from chimera.config import Settings, get_settings
from chimera.core import Agent
from chimera.core.agent import GUIDANCE_KEY, AgentResult
from chimera.core.code_session import trim_to_a_safe_boundary
from chimera.interface import ChatSession
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.base import Tool

TIMEOUT = 10.0


# ---- the loop: between steps, never mid tool call ----------------------------------------------


class _Blocking(Tool):
    name = "blocking"
    description = "Waits until released."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.running = False

    def run(self, **kwargs: Any) -> str:
        self.running = True
        self.entered.set()
        try:
            assert self.release.wait(TIMEOUT)
            return "tool output"
        finally:
            self.running = False


class _OneToolThenAnswer:
    def __init__(self) -> None:
        self.seen: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        self.seen.append([dict(m) for m in messages])
        if len(self.seen) == 1:
            return CompletionResult(
                content="", model="fake", tool_calls=[ToolCall(id="c1", name="blocking", arguments={})]
            )
        return CompletionResult(content="done, adjusted", model="fake")


def test_guidance_sent_during_a_tool_call_is_read_after_it_never_inside_it() -> None:
    tool = _Blocking()
    registry = ToolRegistry()
    registry.register(tool)
    backend = _OneToolThenAnswer()
    turns = LiveTurns()
    turns.start(turn_id="t1", session_id="s1", workspace="", message="go", live_since=0)
    drain = turns.take_guidance("t1")
    polled_mid_tool: list[bool] = []

    def take() -> list[str]:
        polled_mid_tool.append(tool.running)
        return drain()

    result: dict[str, AgentResult] = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("r", Agent(backend, registry).run("go", take_guidance=take))
    )
    worker.start()
    assert tool.entered.wait(TIMEOUT)
    assert turns.guide("t1", "use the v2 API instead") == "queued"
    time.sleep(0.1)
    assert len(backend.seen) == 1, "nothing may reach the model while the tool is still running"
    tool.release.set()
    worker.join(TIMEOUT)
    assert not worker.is_alive()

    assert not any(polled_mid_tool), "the queue was read while a tool call was in flight"
    second = backend.seen[1]
    # The order a provider accepts: the call, its answer, THEN the person's words.
    assert second[-3]["role"] == "assistant" and second[-3].get("tool_calls")
    assert second[-2]["role"] == "tool" and second[-2]["content"] == "tool output"
    assert second[-1]["role"] == "user" and second[-1]["content"] == "use the v2 API instead"
    assert second[-1][GUIDANCE_KEY] is True
    assert any(m.get("content") == "use the v2 API instead" for m in result["r"].transcript)


def test_guidance_that_arrives_with_the_final_answer_continues_the_turn() -> None:
    """Text sent while the model wrote its answer would otherwise be silently unread."""

    class Backend:
        def __init__(self) -> None:
            self.seen: list[list[dict[str, Any]]] = []

        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            self.seen.append([dict(m) for m in messages])
            return CompletionResult(content=f"answer {len(self.seen)}", model="fake")

    backend = Backend()
    # Polls in order: the top of step 1 (nothing yet), then the check after its final answer.
    queue: list[list[str]] = [[], ["also update the changelog"]]

    def take() -> list[str]:
        return queue.pop(0) if queue else []

    result = Agent(backend, ToolRegistry()).run("fix it", take_guidance=take)

    assert len(backend.seen) == 2, "the guidance found at the final answer must get its own step"
    assert backend.seen[1][-1]["content"] == "also update the changelog"
    assert result.answer == "answer 2"


def test_the_marker_never_reaches_a_provider() -> None:
    """An unknown key on a message is how a request becomes a 500 (`_to_message_dicts`)."""
    from chimera.providers.gateway import _to_message_dicts

    sent = _to_message_dicts([{"role": "user", "content": "x", GUIDANCE_KEY: True}])
    assert sent == [{"role": "user", "content": "x"}]


# ---- the registry: queued, refused, and what the receipt gets ---------------------------------


def test_guidance_is_refused_until_the_loop_reads_and_after_it_returns() -> None:
    turns = LiveTurns()
    assert turns.guide("nope", "x") == "not_running"
    turns.start(turn_id="t", session_id="s", workspace="", message="m", live_since=0)
    # Running, but no loop is reading yet (an external agent's turn never will): not "queued".
    assert turns.guide("t", "early") == "not_steerable"
    take = turns.take_guidance("t")
    assert take() == []
    assert turns.guide("t", "first") == "queued"
    assert take() == ["first"]
    assert turns.guide("t", "too late") == "queued"

    read, unread = turns.close_guidance("t")

    assert [(r["text"], r["author"], r["role"], r["tainted"]) for r in read] == [
        ("first", "owner", "user", False)
    ]
    assert isinstance(read[0]["read_at"], float)
    assert unread == ["too late"], "sent after the last read must be reported as unread"
    assert turns.guide("t", "after") == "not_running"


# ---- the route and the receipt ------------------------------------------------------------------


class _SteerableAgent:
    """Works until it reads guidance, then answers with it in the transcript."""

    entered = threading.Event()
    release = threading.Event()

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config")

    def run(self, task: str, should_stop: Any = None, take_guidance: Any = None,
            history: Any = None, **_kw: Any) -> AgentResult:
        type(self).entered.set()
        got: list[str] = []
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline and not got and not type(self).release.is_set():
            got = take_guidance() if take_guidance is not None else []
            time.sleep(0.01)
        transcript: list[dict[str, Any]] = [*(history or []), {"role": "user", "content": task}]
        transcript += [{"role": "user", "content": g, GUIDANCE_KEY: True} for g in got]
        transcript.append({"role": "assistant", "content": "done"})
        return AgentResult(answer="done", steps=1, stopped_reason="final", transcript=transcript)


@pytest.fixture
def steerable() -> Any:
    _SteerableAgent.entered.clear()
    _SteerableAgent.release.clear()
    yield _SteerableAgent
    _SteerableAgent.release.set()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(agent()), workspace=ws, settings=settings))


def _wait(predicate: Any) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_guidance_posted_to_a_running_turn_is_on_its_receipt_as_the_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steerable: Any
) -> None:
    client = _client(tmp_path, monkeypatch, steerable)
    thread = threading.Thread(
        target=lambda: client.post("/api/code/turn", json={"message": "refactor login"}), daemon=True
    )
    thread.start()
    assert steerable.entered.wait(TIMEOUT)
    running = client.get("/api/code/turns/running").json()
    turn_id, session_id = running[0]["turn_id"], running[0]["session_id"]

    reply = client.post(f"/api/code/turns/{turn_id}/guidance", json={"text": "keep the old API"})

    assert reply.status_code == 200, reply.text
    assert reply.json() == {"turn_id": turn_id, "queued": True}
    thread.join(TIMEOUT)
    assert _wait(lambda: client.get("/api/code/turns/running").json() == [])
    exchanges = client.get(f"/api/code/sessions/{session_id}").json()["exchanges"]
    # One turn, with its correction inside it — not two turns, which would also shift the receipts.
    assert len(exchanges) == 1
    assert exchanges[0]["you"] == "refactor login"
    assert exchanges[0]["guidance"] == ["keep the old API"]
    [entry] = exchanges[0]["done"]["guidance"]
    assert entry["text"] == "keep the old API"
    assert entry["role"] == "user" and entry["author"] == "owner" and entry["tainted"] is False
    # And the live record a terminal follows says when it was read.
    frames = client.get(f"/api/code/turns/{turn_id}").json()["frames"]
    assert [f["text"] for f in frames if f["event"] == "guidance"] == ["keep the old API"]

    # A finished turn refuses, with a reason, rather than answering "queued" for text nobody reads.
    late = client.post(f"/api/code/turns/{turn_id}/guidance", json={"text": "and this?"})
    assert late.status_code == 409
    assert "not running" in late.json()["detail"]


def test_guidance_to_an_unknown_turn_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steerable: Any
) -> None:
    client = _client(tmp_path, monkeypatch, steerable)
    reply = client.post("/api/code/turns/nope/guidance", json={"text": "x"})
    assert reply.status_code == 409
    assert client.post("/api/code/turns/nope/guidance", json={"text": ""}).status_code == 422


# ---- reopening: a correction is part of its turn -----------------------------------------------


def test_a_reopened_conversation_keeps_one_turn_and_its_receipt_where_it_was() -> None:
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
        {"role": "user", "content": "not that file", GUIDANCE_KEY: True},
        {"role": "assistant", "content": "two"},
    ]
    exchanges = attach_receipts(
        exchanges_from_messages(messages), [{"steps": 1, "n": "r1"}, {"steps": 2, "n": "r2"}]
    )
    assert [e["you"] for e in exchanges] == ["first", "second"]
    assert exchanges[1]["guidance"] == ["not that file"]
    assert exchanges[0]["done"]["n"] == "r1" and exchanges[1]["done"]["n"] == "r2"
    # A long conversation is never cut at a correction, which would drop the request it corrects.
    assert trim_to_a_safe_boundary(messages, 2)[0]["content"] == "second"


# ---- the bridge: guidance is held where sending a message is ------------------------------------


def test_the_bridge_steers_a_turn_only_where_it_could_send_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Steering by turn id is driving that turn's folder; a turn the app started in its install
    folder (Chimera's `.env` beside it) is not reachable from the bridge one message at a time."""
    from tests.test_the_bridge_never_hands_a_run_chimeras_env_or_data import _desktop
    from tests.test_the_desktop_bridge_reaches_only_its_table import _call

    app = _desktop(tmp_path, monkeypatch, full=False)
    turns = app.state.live_turns
    project = tmp_path / "proj"
    project.mkdir()
    for turn_id, folder in (("inst", tmp_path / "install"), ("proj", project)):
        turns.start(turn_id=turn_id, session_id=turn_id, workspace=str(folder), message="m",
                    live_since=0)
        turns.take_guidance(turn_id)()  # the loop is reading
    with TestClient(app) as client:
        refused = _call(client, app, "conversations.guidance", params={"turn_id": "inst"},
                        body={"text": "x"}, wait_seconds=0)
        allowed = _call(client, app, "conversations.guidance", params={"turn_id": "proj"},
                        body={"text": "use v2"}, wait_seconds=0)
    get_settings.cache_clear()

    assert refused.status_code == 403, refused.text
    assert "own data or its .env" in refused.json()["detail"]
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"] == {"turn_id": "proj", "queued": True}
    assert turns.take_guidance("proj")() == ["use v2"]
    assert turns.take_guidance("inst")() == []
