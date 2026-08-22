"""The route half of "a failing turn still has to say what it paid".

`tests/test_failed_turn_still_billed.py` proves the agent carries the number out on the exception.
This proves the endpoint writes it down — the two halves were separately absent, and the agent-side
one alone would be a mechanism with no consumer, which is the shape of defect this project keeps
finding in its own code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import PartialSpend, _SPEND_ATTR  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _DiesHavingSpent:
    """Fails the way a provider does, after the run already paid for two calls."""

    SPEND = PartialSpend(
        prompt_tokens=6000, completion_tokens=1000, usd=0.0123,
        model="openrouter/deepseek/deepseek-chat", steps=2,
    )

    def __init__(self, spend: PartialSpend | None) -> None:
        self.spend = spend
        self.run_state = RunState()

    def run(self, task: str, **kwargs: Any) -> Any:
        exc = RuntimeError("upstream said no")
        if self.spend is not None:
            setattr(exc, _SPEND_ATTR, self.spend)
        raise exc


def _client(tmp_path: Path, agent: Any, monkeypatch: Any) -> tuple[TestClient, Path]:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    home = tmp_path / "home"
    settings = Settings(CHIMERA_HOME=str(home))
    import chimera.core

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: agent, raising=True)
    client = TestClient(
        build_api_app(lambda: ChatSession(agent), workspace=ws, settings=settings)
    )
    return client, home / "usage.jsonl"


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_turn_that_died_after_spending_lands_in_the_usage_log(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, usage = _client(tmp_path, _DiesHavingSpent(_DiesHavingSpent.SPEND), monkeypatch)

    response = client.post("/api/code/turn", json={"message": "build the thing"})

    assert response.status_code == 200
    assert "event: error" in response.text, "the client still has to be told it failed"

    rows = _rows(usage)
    assert len(rows) == 1, "nineteen kilobytes of paid-for output left no row at all"
    assert rows[0]["prompt_tokens"] == 6000
    assert rows[0]["completion_tokens"] == 1000
    assert rows[0]["model"] == "openrouter/deepseek/deepseek-chat"
    assert rows[0]["usd"] == pytest.approx(0.0123)


def test_a_turn_that_died_before_spending_writes_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # A model name that does not exist fails on the first call. An empty log is the correct answer
    # here, and a fix that wrote a zero row would replace a silent undercount with an invented
    # entry — worse, because nothing downstream can tell an invented row from a real one.
    client, usage = _client(tmp_path, _DiesHavingSpent(None), monkeypatch)

    client.post("/api/code/turn", json={"message": "build the thing"})

    assert _rows(usage) == []


def test_a_zero_spend_carrier_is_also_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    # The carrier exists but says nothing was spent — a run that raised between starting and its
    # first completion. Without this case the guard could be `spent is not None` and the empty row
    # would come back through the other door.
    empty = PartialSpend(prompt_tokens=0, completion_tokens=0, usd=0.0, model="", steps=0)
    client, usage = _client(tmp_path, _DiesHavingSpent(empty), monkeypatch)

    client.post("/api/code/turn", json={"message": "build the thing"})

    assert _rows(usage) == []
