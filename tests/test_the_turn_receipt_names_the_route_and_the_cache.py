"""The Code screen's turn receipt says which route answered and what its cache served.

Found by using the product, not by reading it: the live test of 0.57.0 ran the same request twice
on the same install — once as an autonomous run, once as a Code-screen turn. The run's receipt named
its route (`Relace`, then `StreamLake`, with the cache going from 55,166 to 0 tokens between the
two); the turn's receipt, written by the same backend a minute later, had neither field. The release
notes said "every attempt receipt". The surface most people use was the one that did not.

Two receipts, one definition: `StepLog.cache_read_tokens` and `StepLog.provider` are now where the
numbers come from, and `autonomous.py`'s helpers read them off the same place, so the run receipt and
the turn receipt cannot answer differently for the same steps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.steplog import StepLog, StepRecord
from chimera.interface import ChatSession


def _log(*steps: tuple[int | None, str]) -> StepLog:
    log = StepLog()
    for cached, provider in steps:
        log.add(
            StepRecord(
                index=len(log.steps),
                prompt_tokens=100,
                completion_tokens=10,
                model="test/model",
                cached_tokens=cached,
                provider=provider,
            )
        )
    return log


class _RoutedAgent:
    """An agent whose steps name a route and report cache usage, like a real OpenRouter turn."""

    def __init__(self, log: StepLog) -> None:
        self.log = log

    def run(self, task: str, **_: Any) -> AgentResult:
        return AgentResult(
            answer="hello",
            steps=len(self.log.steps),
            stopped_reason="final",
            transcript=[
                {"role": "user", "content": task},
                {"role": "assistant", "content": "hello"},
            ],
            model="test/model",
            steplog=self.log,
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: StepLog) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _RoutedAgent(log), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_RoutedAgent(log)), workspace=ws, settings=settings)
    )


def _frames(response: Any) -> dict[str, dict[str, Any]]:
    """The last frame of each kind, by event name."""
    event, out = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def _done(response: Any) -> dict[str, Any]:
    return _frames(response)["done"]


def test_the_steplog_is_the_one_definition() -> None:
    """The run receipt's helpers and the turn receipt read the same two properties."""
    from chimera.core.autonomous import _cache_read_tokens, _provider

    log = _log((None, ""), (1200, "DeepSeek"), (300, "Other"))
    assert log.cache_read_tokens == 1500
    assert log.provider == "DeepSeek"  # the first route named, the one the trajectory was set by
    assert _cache_read_tokens(log) == log.cache_read_tokens
    assert _provider(log) == log.provider
    silent = _log((None, ""), (None, "DeepSeek"))
    assert silent.cache_read_tokens is None  # a silent route, never zero
    assert silent.provider == "DeepSeek"
    assert StepLog().provider == ""


def test_the_turn_receipt_carries_the_route_and_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, _log((39424, "Relace"), (55166, "Relace")))
    response = client.post("/api/code/turn", json={"message": "oi"})
    assert response.status_code == 200
    done = _done(response)
    assert done["provider"] == "Relace"
    assert done["cache_read_tokens"] == 39424 + 55166

    # And it is on the receipt the conversation keeps, which is what a person reopens later.
    session_id = _frames(response)["session"]["session_id"]
    receipt = client.get(f"/api/code/sessions/{session_id}").json()["exchanges"][-1]["done"]
    assert receipt["provider"] == "Relace"
    assert receipt["cache_read_tokens"] == 39424 + 55166


def test_a_silent_route_is_absent_on_the_turn_receipt_not_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second live run: the route changed and the cache reported 0 — a real zero, which the
    receipt must keep apart from a route that never said anything at all."""
    client = _client(tmp_path, monkeypatch, _log((None, ""), (None, "")))
    done = _done(client.post("/api/code/turn", json={"message": "oi"}))
    assert done["provider"] == ""
    assert done["cache_read_tokens"] is None

    (tmp_path / "second").mkdir(exist_ok=True)
    client = _client(tmp_path / "second", monkeypatch, _log((0, "StreamLake")))
    done = _done(client.post("/api/code/turn", json={"message": "oi"}))
    assert done["provider"] == "StreamLake"
    assert done["cache_read_tokens"] == 0
