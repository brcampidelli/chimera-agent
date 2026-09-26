"""What the memory extraction costs reaches the usage log, filed with the conversation it was for.

The extraction is one more model call per turn (study 25 S13), and a call nobody records is spend
the Cost screen cannot see: `chimera.orchestration.metering` exists because the calls made FOR a
run, not by its worker, were once priced nowhere. The extraction goes through the same meter and is
written with `record_spend`, like the orchestration and batch rows, under the turn's own session id.

It is marked as a call made for a turn, not a turn: its tokens and price count everywhere, but the
Cost screen's turn count does not double because extraction is on.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import chimera.fusion.receipts as receipts
from chimera.api.usage import MEMORY_KIND, UsageRecord, load_usage, summarize_usage
from chimera.fusion.receipts import ModelPrice
from chimera.memory.extract import MemoryExtractor
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore

MODEL = "test/memx-priced"
#: 1,000 prompt tokens at $1/M plus 100 completion tokens at $2/M.
COST = 0.0012


@dataclass
class _Reply:
    content: str
    model: str = MODEL
    prompt_tokens: int = 1000
    completion_tokens: int = 100


class _Model:
    def __init__(self, content: str | None = None) -> None:
        self.content = content if content is not None else json.dumps({"operations": [
            {"op": "add", "fact": "The user is allergic to peanuts.",
             "evidence": "I'm allergic to peanuts"},
        ]})

    def complete(self, messages: Any, **kwargs: Any) -> _Reply:
        return _Reply(self.content)


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """One exact price for the fake model, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(receipts, "_PRICES", [(MODEL, ModelPrice(1.0, 2.0)), *receipts._PRICES])


def _extractor(tmp_path: Path, backend: Any, usage_id: Any = "conv-1") -> MemoryExtractor:
    memory = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    return MemoryExtractor(memory, backend, background=False, usage_home=tmp_path / "home",
                           usage_id=usage_id)


def _rows(tmp_path: Path) -> list[UsageRecord]:
    return load_usage(tmp_path / "home" / "usage.jsonl")


def test_the_call_is_priced_and_filed_under_the_conversation(tmp_path: Path) -> None:
    extractor = _extractor(tmp_path, _Model())

    extractor.after_turn("I'm allergic to peanuts, snack ideas?", "Crackers.")

    [row] = _rows(tmp_path)
    assert (row.session_id, row.route_kind, row.model) == ("conv-1", MEMORY_KIND, MODEL)
    assert (row.prompt_tokens, row.completion_tokens) == (1000, 100)
    assert row.usd == pytest.approx(COST)
    assert extractor.last is not None and extractor.last.usd == pytest.approx(COST)


def test_a_reply_that_could_not_be_read_was_still_paid_for(tmp_path: Path) -> None:
    extractor = _extractor(tmp_path, _Model("not json at all"))

    extractor.after_turn("I'm allergic to peanuts", "Noted.")

    [row] = _rows(tmp_path)
    assert row.usd == pytest.approx(COST)
    assert extractor.last is not None and extractor.last.error


def test_a_call_that_never_returned_costs_nothing_and_writes_no_row(tmp_path: Path) -> None:
    class _Down:
        def complete(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("provider is down")

    extractor = _extractor(tmp_path, _Down())

    extractor.after_turn("I'm allergic to peanuts", "Noted.")

    assert _rows(tmp_path) == []


def test_the_conversation_id_is_read_when_the_spend_is_written(tmp_path: Path) -> None:
    """A terminal thread can be replaced by `/new` after the extractor was built."""
    current = ["thread-a"]
    extractor = _extractor(tmp_path, _Model(), usage_id=lambda: current[0])
    current[0] = "thread-b"

    extractor.after_turn("I'm allergic to peanuts", "Noted.")

    assert [r.session_id for r in _rows(tmp_path)] == ["thread-b"]


def test_an_extraction_adds_to_the_spend_and_not_to_the_turns() -> None:
    turn = UsageRecord(ts="2026-09-25T10:00:00+00:00", session_id="conv-1", model="m/chat",
                       prompt_tokens=5000, completion_tokens=500, usd=0.01)
    extraction = UsageRecord(ts="2026-09-25T10:00:05+00:00", session_id="conv-1", model=MODEL,
                             prompt_tokens=1000, completion_tokens=100, usd=COST,
                             route_kind=MEMORY_KIND)

    summary = summarize_usage([turn, extraction])

    totals = summary["totals"]
    assert totals["turns"] == 1
    assert totals["usd"] == pytest.approx(0.01 + COST)
    assert totals["prompt_tokens"] == 6000
    [session] = summary["by_session"]
    assert (session["turns"], session["usd"]) == (1, pytest.approx(0.01 + COST))
    [day] = summary["by_day"]
    assert day["turns"] == 1
    assert summary["route_mix"] == {"single": 1, "fusion": 0, "cascade": 0}
    # Per model a row is a call answered, so the extraction's model is not a group of zero turns.
    assert {m["model"]: m["turns"] for m in summary["by_model"]} == {"m/chat": 1, MODEL: 1}


def test_through_the_code_turn_the_extraction_is_filed_beside_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.memory.extract as module
    from tests.test_memory_extraction_never_costs_the_turn import _client, _frames

    # The real extractor, run inline with the fake model so the row exists when the turn returns.
    monkeypatch.setattr(module, "MemoryExtractor", functools.partial(
        module.MemoryExtractor, backend=_Model(), background=False
    ))
    memory = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    client, _built = _client(tmp_path, monkeypatch, extract=True, memory=memory)

    frames = _frames(client.post("/api/code/turn", json={"message": "I'm allergic to peanuts"}))

    session_id = frames["session"]["session_id"]
    rows = load_usage(tmp_path / "home" / "usage.jsonl")
    assert [(r.session_id, r.route_kind) for r in rows] == [
        (session_id, None), (session_id, MEMORY_KIND)
    ]
    assert rows[1].usd == pytest.approx(COST)
    assert [i.content for i in memory.store.all()] == ["The user is allergic to peanuts."]
