"""The Code turn receipt says why the cache did not answer, and what an idle compaction saved.

Built on the same stub as the route-and-cache receipt test: the first turn of a conversation has no
earlier turn to compare with, so its miss is `first_turn`, and a silent provider names no cause at all.
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


def _log(cached: int | None) -> StepLog:
    log = StepLog()
    log.add(
        StepRecord(
            index=0,
            prompt_tokens=100,
            completion_tokens=10,
            model="anthropic/claude-haiku-5.5",
            cached_tokens=cached,
            provider="Anthropic",
            system_fingerprint="sys",
            tools_fingerprint="tools",
            prefix_fingerprint="pre",
        )
    )
    return log


class _Agent:
    config = type("C", (), {"model": "anthropic/claude-haiku-5.5", "max_usd": None, "warn_usd": None})()

    def __init__(self, log: StepLog) -> None:
        self.log = log

    def run(self, task: str, **_: Any) -> AgentResult:
        return AgentResult(
            answer="ola",
            steps=1,
            stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ola"}],
            model="anthropic/claude-haiku-5.5",
            steplog=self.log,
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: StepLog) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _Agent(log), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_Agent(log)), workspace=ws, settings=settings)
    )


def _done(response: Any) -> dict[str, Any]:
    event, out = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out["done"]


def test_the_first_turn_names_its_miss_as_a_first_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, _log(cached=0))
    done = _done(client.post("/api/code/turn", json={"message": "oi"}))
    assert done["cache_read_tokens"] == 0
    assert done["cache_miss_cause"] == "first_turn"
    assert done["cache_gap_seconds"] is None
    # The provider reported cache usage (a read of zero), so a write of zero is a reported zero, not silence.
    assert done["cache_write_tokens"] == 0
    assert done["idle_compaction"] is None


def test_a_turn_that_read_the_cache_names_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, _log(cached=4096))
    done = _done(client.post("/api/code/turn", json={"message": "oi"}))
    assert done["cache_read_tokens"] == 4096
    assert done["cache_miss_cause"] == "none"


def test_a_silent_provider_names_no_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch, _log(cached=None))
    done = _done(client.post("/api/code/turn", json={"message": "oi"}))
    assert done["cache_read_tokens"] is None
    assert done["cache_miss_cause"] is None
