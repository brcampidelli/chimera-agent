"""What tidying memory costs goes on the bill of the Code turn that tidied it.

With "Remember from chat" and "Tidy memory" both on, a turn whose message says "remember that…"
writes the fact and then, when memory has outgrown its budget, asks a model to merge each cluster of
similar facts (`code_api._remember_and_tidy`, `memory.consolidate.model_summarizer`). That call was
made on a fresh gateway nothing metered: the turn's row, its receipt and the Cost screen read the
turn as if it had made no such call.

The merge runs on the turn's thread, inside the turn's last step and before `done`, so it goes into
the turn's own row, the way the plan gate's call does. A tidying turn is still one turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import chimera.fusion.receipts as receipts
from chimera.api.usage import UsageRecord, load_usage, summarize_usage
from chimera.config import Settings
from chimera.core import Agent
from chimera.fusion.receipts import ModelPrice
from chimera.interface import ChatSession
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore
from chimera.providers.gateway import CompletionResult
from chimera.tools import ToolRegistry

MERGE_MODEL = "test/merge-priced"  # $1/M in, $2/M out
TURN_MODEL = "test/turn-priced"  # $3/M in, $4/M out
#: 500 prompt tokens at $1/M plus 50 completion tokens at $2/M.
MERGE_COST = 0.0006
#: 10,000 prompt tokens at $3/M plus 1,000 completion tokens at $4/M.
TURN_COST = 0.034


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact prices for the fake models, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(receipts, "_PRICES", [
        (MERGE_MODEL, ModelPrice(1.0, 2.0)), (TURN_MODEL, ModelPrice(3.0, 4.0)),
        *receipts._PRICES,
    ])


class _Gateway:
    """The turn's gateway. The merge is told apart by what `model_summarizer` asks for."""

    merge_model = MERGE_MODEL
    merge_boom = False
    calls: list[str] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        last = messages[-1]
        text = str(last.get("content") if isinstance(last, dict) else last.content)
        if text.startswith("Merge these related facts"):
            _Gateway.calls.append("merge")
            if _Gateway.merge_boom:
                raise RuntimeError("the provider fell over")
            return CompletionResult(content="The user indents with tabs.",
                                    model=_Gateway.merge_model,
                                    prompt_tokens=500, completion_tokens=50)
        _Gateway.calls.append("turn")
        return CompletionResult(content="Noted.", model=TURN_MODEL, prompt_tokens=10_000,
                                completion_tokens=1000)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> type[_Gateway]:
    monkeypatch.setattr(_Gateway, "merge_model", MERGE_MODEL)
    monkeypatch.setattr(_Gateway, "merge_boom", False)
    monkeypatch.setattr(_Gateway, "calls", [])
    return _Gateway


def _turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str) -> str:
    """One Code turn with both memory toggles on, over a memory already past its budget of two."""
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    memory = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    # Two facts close enough to be one cluster, and one that is not: the merge has one thing to do.
    memory.add("The user prefers tabs for indentation", "semantic")
    memory.add("The user prefers tabs for indentation in Python", "semantic")
    memory.add("The user lives in Belo Horizonte", "semantic")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(  # type: ignore[call-arg]
        CHIMERA_HOME=str(home), CHIMERA_CHAT_MEMORY=True, CHIMERA_AUTO_CONSOLIDATE=True,
        CHIMERA_MEMORY_BUDGET=2,
    )
    client = TestClient(build_api_app(
        lambda: ChatSession(Agent(_Gateway(), ToolRegistry())), workspace=ws, settings=settings,
        memory=memory,
    ))
    return client.post("/api/code/turn", json={"message": message, "stream": False}).text


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


def test_a_turn_that_tidied_memory_carries_the_merge_on_its_own_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    text = _turn(tmp_path, monkeypatch, "remember that my flight is on the 12th")

    assert gateway.calls == ["turn", "merge"]
    done = _done(text)
    assert done["memory_consolidated"] == 1
    [row] = _rows(tmp_path)
    assert (row.prompt_tokens, row.completion_tokens) == (10_500, 1050)
    assert row.usd == pytest.approx(TURN_COST + MERGE_COST)
    assert row.model == TURN_MODEL
    assert (done["prompt_tokens"], done["usd"]) == (10_500, pytest.approx(TURN_COST + MERGE_COST))


def test_on_the_cost_screen_a_tidying_turn_is_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    _turn(tmp_path, monkeypatch, "remember that my flight is on the 12th")

    totals = summarize_usage(_rows(tmp_path))["totals"]

    assert totals["turns"] == 1
    assert totals["usd"] == pytest.approx(TURN_COST + MERGE_COST)


def test_a_turn_that_wrote_nothing_made_no_merge_and_is_billed_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    _turn(tmp_path, monkeypatch, "what is the capital of France?")

    assert gateway.calls == ["turn"]
    [row] = _rows(tmp_path)
    assert row.usd == pytest.approx(TURN_COST)


def test_a_merge_call_that_raised_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    gateway.merge_boom = True

    text = _turn(tmp_path, monkeypatch, "remember that my flight is on the 12th")

    assert gateway.calls == ["turn", "merge"]
    assert _done(text)["memory_consolidated"] == 0
    [row] = _rows(tmp_path)
    assert (row.prompt_tokens, row.usd) == (10_000, pytest.approx(TURN_COST))


def test_an_unpriced_merge_makes_the_turns_price_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    gateway.merge_model = "test/no-price-anywhere"

    _turn(tmp_path, monkeypatch, "remember that my flight is on the 12th")

    [row] = _rows(tmp_path)
    assert row.usd is None, "an unpriced call must never read as a low total"
