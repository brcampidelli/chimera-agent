"""Stop ends a coding turn on the server, not only the screen's view of it.

Found reading the code on 2026-09-30, while mapping how several conversations run at once: the Stop
button aborted the screen's request and nothing else. There was no route to stop a coding turn, and
`should_stop` reached the agent loop only for background works. So a runaway turn the person had
"stopped" kept calling the model, editing files and spending until it finished on its own, while the
screen said it had stopped.

Now `POST /api/code/turns/{turn_id}/stop` raises the turn's stop signal. A turn on Chimera's own loop
polls it once per step; a turn run by an external agent (ACP) has its prompt cancelled.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.live_turns import LiveTurns
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 10.0


class _Loop:
    """An agent that works step after step until it is told to stop, or released."""

    entered = threading.Event()
    release = threading.Event()
    saw_stop = threading.Event()
    got_signal = threading.Event()

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, should_stop: Any = None, **_kw: Any) -> AgentResult:
        type(self).entered.set()
        if should_stop is not None:
            type(self).got_signal.set()
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline and not type(self).release.is_set():
            if should_stop is not None and should_stop():
                type(self).saw_stop.set()
                return AgentResult(answer="stopped", steps=1, stopped_reason="cancelled", transcript=[])
            time.sleep(0.01)
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


@pytest.fixture
def agent() -> type[_Loop]:
    for flag in (_Loop.entered, _Loop.release, _Loop.saw_stop, _Loop.got_signal):
        flag.clear()
    yield _Loop
    _Loop.release.set()


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


def _start(client: TestClient, body: dict[str, Any]) -> threading.Thread:
    thread = threading.Thread(target=lambda: client.post("/api/code/turn", json=body), daemon=True)
    thread.start()
    return thread


def test_a_stop_ends_the_running_turn_on_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type[_Loop]
) -> None:
    client = _client(tmp_path, monkeypatch, agent)
    thread = _start(client, {"message": "refactor everything"})
    assert agent.entered.wait(TIMEOUT)
    assert agent.got_signal.is_set(), "an interactive turn must hand the agent loop a stop signal"
    running = client.get("/api/code/turns/running").json()
    assert len(running) == 1

    reply = client.post(f"/api/code/turns/{running[0]['turn_id']}/stop")

    assert reply.status_code == 200, reply.text
    assert reply.json() == {"turn_id": running[0]["turn_id"], "stopping": True}
    assert agent.saw_stop.wait(TIMEOUT), "the agent loop never saw the stop"
    thread.join(TIMEOUT)
    assert not thread.is_alive(), "the turn kept running after it was stopped"
    assert _wait(lambda: client.get("/api/code/turns/running").json() == [])


def test_without_a_stop_the_turn_keeps_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type[_Loop]
) -> None:
    """The control: the loop above ends because it was told to, not because it ends anyway."""
    client = _client(tmp_path, monkeypatch, agent)
    thread = _start(client, {"message": "refactor everything"})
    assert agent.entered.wait(TIMEOUT)
    time.sleep(0.3)
    assert thread.is_alive()
    assert not agent.saw_stop.is_set()
    agent.release.set()
    thread.join(TIMEOUT)


def test_stopping_a_turn_that_is_not_running_is_a_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type[_Loop]
) -> None:
    client = _client(tmp_path, monkeypatch, agent)
    reply = client.post("/api/code/turns/nope/stop")
    assert reply.status_code == 404
    # The route's own answer, not the router's "Not Found" for a path that does not exist.
    assert reply.json()["detail"] == "no such running turn"


def test_a_turn_run_by_an_external_agent_has_its_prompt_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type[_Loop]
) -> None:
    import chimera.api.code_acp as code_acp

    cancelled = threading.Event()
    entered = threading.Event()

    def run_external_turn(**_kw: Any) -> Any:
        entered.set()
        assert cancelled.wait(TIMEOUT), "the external agent's prompt was never cancelled"
        raise RuntimeError("cancelled by the person")

    def cancel_external_turn(**kw: Any) -> bool:
        assert kw["provider"] == "claude"
        cancelled.set()
        return True

    monkeypatch.setattr(code_acp, "run_external_turn", run_external_turn)
    monkeypatch.setattr(code_acp, "cancel_external_turn", cancel_external_turn)
    client = _client(tmp_path, monkeypatch, agent)
    thread = _start(client, {"message": "fix the build", "provider": "claude"})
    assert entered.wait(TIMEOUT)
    turn_id = client.get("/api/code/turns/running").json()[0]["turn_id"]

    assert client.post(f"/api/code/turns/{turn_id}/stop").status_code == 200

    assert cancelled.wait(TIMEOUT)
    thread.join(TIMEOUT)
    assert not thread.is_alive()


def test_the_registry_holds_one_stop_per_turn_and_forgets_it_when_the_turn_ends() -> None:
    turns = LiveTurns()
    assert turns.request_stop("t1") is False, "nothing to stop"
    turns.start(turn_id="t1", session_id="s1", workspace="w", message="m", live_since=0)
    turns.start(turn_id="t2", session_id="s2", workspace="w", message="m", live_since=0)
    stop_one, stop_two = turns.should_stop("t1"), turns.should_stop("t2")
    calls: list[str] = []
    turns.on_stop("t1", lambda: calls.append("cancel"))

    assert turns.request_stop("t1") is True

    assert stop_one() is True
    assert stop_two() is False, "a stop reaches only the turn it names"
    assert calls == ["cancel"]
    late: list[str] = []
    turns.on_stop("t1", lambda: late.append("cancel"))
    assert late == ["cancel"], "a cancel registered after the stop runs at once"
    turns.finish("t1")
    assert turns.request_stop("t1") is False
