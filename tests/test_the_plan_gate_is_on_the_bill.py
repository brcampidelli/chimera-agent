"""What the plan gate's model call costs goes on the bill of the turn it gated.

`plan_gate` asks a model for a plan before the Code turn runs anything (`chimera/api/plan_gate.py`),
one call on the turn's own backend. Nothing metered it. Verified before this change, on a fake model
that answers the plan with 1,000/100 tokens: an approved turn's row and receipt carried only the
loop's tokens, and a refused one wrote a row of zero tokens at an unknown price, although the one
call it made had a known price.

The call is made on the turn's thread before the turn's row is written, so it goes into that row:
one meter for the one call, added to what the loop spent. A gated turn is still one turn on the
Cost screen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import chimera.fusion.receipts as receipts
from chimera.api.plan_gate import _PLAN_GATE_SYSTEM
from chimera.api.usage import UsageRecord, load_usage, summarize_usage
from chimera.config import Settings
from chimera.core import Agent
from chimera.fusion.receipts import ModelPrice
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult
from chimera.tools import ToolRegistry

PLAN_MODEL = "test/plan-priced"  # $1/M in, $2/M out
TURN_MODEL = "test/turn-priced"  # $3/M in, $4/M out
#: 1,000 prompt tokens at $1/M plus 100 completion tokens at $2/M.
PLAN_COST = 0.0012
#: 10,000 prompt tokens at $3/M plus 1,000 completion tokens at $4/M.
TURN_COST = 0.034


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact prices for the fake models, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(receipts, "_PRICES", [
        (PLAN_MODEL, ModelPrice(1.0, 2.0)), (TURN_MODEL, ModelPrice(3.0, 4.0)), *receipts._PRICES,
    ])


class _Gateway:
    """The turn's `LLMGateway`, built once per turn, so what it does is set on the class.

    The planning call is told apart by its system prompt. ``plan`` is the planner's reply and
    ``plan_model`` who answers it; ``plan_boom`` and ``turn_boom`` make that call raise, the way
    a provider does.
    """

    plan = "1. read a.py\n2. edit a.py"
    plan_model = PLAN_MODEL
    plan_boom = False
    turn_boom = False
    calls: list[str] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        first = messages[0]
        system = first.get("content") if isinstance(first, dict) else first.content
        if system == _PLAN_GATE_SYSTEM:
            _Gateway.calls.append("plan")
            if _Gateway.plan_boom:
                raise RuntimeError("the provider fell over")
            return CompletionResult(content=_Gateway.plan, model=_Gateway.plan_model,
                                    prompt_tokens=1000, completion_tokens=100)
        _Gateway.calls.append("turn")
        if _Gateway.turn_boom:
            raise RuntimeError("the provider fell over")
        return CompletionResult(content="done", model=TURN_MODEL, prompt_tokens=10_000,
                                completion_tokens=1000)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> type[_Gateway]:
    for name, value in (("plan", "1. read a.py\n2. edit a.py"), ("plan_model", PLAN_MODEL),
                        ("plan_boom", False), ("turn_boom", False)):
        monkeypatch.setattr(_Gateway, name, value)
    monkeypatch.setattr(_Gateway, "calls", [])
    return _Gateway


def _turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, approve: bool) -> Any:
    """One gated Code turn, with the person answering ``approve``. Returns the response text."""
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    monkeypatch.setattr("chimera.governance.pending.ask_durably", lambda *_a, **_k: approve)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(build_api_app(
        lambda: ChatSession(Agent(_Gateway(), ToolRegistry())), workspace=ws, settings=settings
    ))
    return client.post("/api/code/turn", json={
        "message": "fix a.py", "stream": False, "plan_gate": True,
    }).text


def _rows(tmp_path: Path) -> list[UsageRecord]:
    return load_usage(tmp_path / "home" / "usage.jsonl")


def _done(text: str) -> dict[str, Any]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: ") and event == "done":
            out = json.loads(line[len("data: "):])
    return out


def test_an_approved_turn_carries_its_plan_call_on_its_own_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    text = _turn(tmp_path, monkeypatch, approve=True)

    assert gateway.calls == ["plan", "turn"]
    [row] = _rows(tmp_path)
    assert (row.prompt_tokens, row.completion_tokens) == (11_000, 1100)
    assert row.usd == pytest.approx(PLAN_COST + TURN_COST)
    assert row.model == TURN_MODEL, "the row names the model that did the work"
    done = _done(text)
    assert (done["prompt_tokens"], done["usd"]) == (11_000, pytest.approx(PLAN_COST + TURN_COST))


def test_a_refused_turn_is_billed_the_plan_it_paid_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    text = _turn(tmp_path, monkeypatch, approve=False)

    assert gateway.calls == ["plan"]
    [row] = _rows(tmp_path)
    assert (row.prompt_tokens, row.completion_tokens) == (1000, 100)
    assert row.usd == pytest.approx(PLAN_COST)
    assert row.model == PLAN_MODEL, "a row with dollars names the model that earned them"
    assert _done(text)["stopped_reason"] == "plan_gate:refused"


def test_a_plan_with_no_steps_was_still_paid_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    gateway.plan = "   \n  "

    text = _turn(tmp_path, monkeypatch, approve=True)

    assert _done(text)["stopped_reason"] == "plan_gate:no_plan"
    [row] = _rows(tmp_path)
    assert row.usd == pytest.approx(PLAN_COST)


def test_a_planning_call_that_raised_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    gateway.plan_boom = True

    text = _turn(tmp_path, monkeypatch, approve=True)

    assert "event: error" in text
    assert _rows(tmp_path) == []


def test_an_unpriced_plan_makes_the_turns_price_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    gateway.plan_model = "test/no-price-anywhere"

    _turn(tmp_path, monkeypatch, approve=True)

    [row] = _rows(tmp_path)
    assert row.usd is None, "an unpriced call must never read as a low total"
    assert row.prompt_tokens == 11_000


def test_a_run_that_died_after_an_approved_plan_still_bills_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    gateway.turn_boom = True

    text = _turn(tmp_path, monkeypatch, approve=True)

    assert "event: error" in text
    [row] = _rows(tmp_path)
    assert (row.prompt_tokens, row.completion_tokens) == (1000, 100)
    assert row.usd == pytest.approx(PLAN_COST)


def test_a_failure_after_the_row_was_written_does_not_bill_the_plan_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    """The row is written first thing in the turn's last step; a failure later in that step reaches
    the same handler a dying run does, and must not write the planning call a second time."""

    def _breaks(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the memory write fell over")

    monkeypatch.setattr("chimera.api.code_api._remember_and_tidy", _breaks)

    text = _turn(tmp_path, monkeypatch, approve=True)

    assert "event: error" in text
    [row] = _rows(tmp_path)
    assert row.usd == pytest.approx(PLAN_COST + TURN_COST)


def test_on_the_cost_screen_a_gated_turn_is_one_turn_that_cost_both_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    _turn(tmp_path, monkeypatch, approve=True)

    summary = summarize_usage(_rows(tmp_path))

    assert summary["totals"]["turns"] == 1
    assert summary["totals"]["usd"] == pytest.approx(PLAN_COST + TURN_COST)
    assert summary["totals"]["unpriced_turns"] == 0
    [session] = summary["by_session"]
    assert (session["turns"], session["usd"]) == (1, pytest.approx(PLAN_COST + TURN_COST))
