from __future__ import annotations

from pathlib import Path

from bench.memory_selection.generate import generate
from bench.memory_selection.items import ITEMS
from chimera.decisions.contract import Decider, Reading
from chimera.memory.history import HistoryIndex
from chimera.tools.history import RecallHistoryTool, rerank_history_hits


class FakeBackend:
    name = "fake"
    model = "fake-selection"

    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.calls = 0

    def instrument(self, question: object) -> str:
        return "fake instrument"

    def ask(self, state: str, question: object) -> Reading:
        self.calls += 1
        return Reading(choice=self.choice, shares=None, p=None)


def test_synthetic_generator_has_fixed_answer_turns(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    generate(path)
    index = HistoryIndex(tmp_path)
    try:
        assert index.count(project="synthetic-memory-selection/q00") == 30
        item = ITEMS[0]
        hits = index.search(item.query, project="synthetic-memory-selection/q00", k=30)
        assert len(hits) == 30
        assert sum(item.answer in hit.answered for hit in hits) == 1
        assert next(hit for hit in hits if item.answer in hit.answered).turn_id == item.relevant_turn
    finally:
        index.close()


def test_fake_backend_reranks_only_to_its_selected_candidate(tmp_path: Path) -> None:
    generate(tmp_path / "history.db")
    index = HistoryIndex(tmp_path)
    try:
        item = ITEMS[0]
        hits = index.search(item.query, project="synthetic-memory-selection/q00", k=30)
        backend = FakeBackend("turn_7")
        ranked = rerank_history_hits(item.query, hits, Decider(backend), limit=1)
        assert ranked[0] is hits[7]
        assert sorted(hit.turn_id for hit in ranked) == sorted(hit.turn_id for hit in hits)
        assert backend.calls == 1
    finally:
        index.close()


def test_recall_history_default_is_byte_identical_and_never_calls_backend(tmp_path: Path) -> None:
    generate(tmp_path / "history.db")
    index = HistoryIndex(tmp_path)
    try:
        item = ITEMS[0]
        tool = RecallHistoryTool(index, project="synthetic-memory-selection/q00")
        default = tool.run(query=item.query, k=3)
        explicit_off = tool.run(query=item.query, k=3, rerank=False)
        assert default.encode("utf-8") == explicit_off.encode("utf-8")
        assert "3 earlier turns match" in default
    finally:
        index.close()


def test_rerank_halt_preserves_original_order() -> None:
    from chimera.memory.history import HistoryHit

    class HaltBackend(FakeBackend):
        def ask(self, state: str, question: object) -> Reading:
            self.calls += 1
            raise RuntimeError("offline")

    hits = [
        HistoryHit(str(i), "s", "p", float(i), False, f"asked {i}", "", [], [], [])
        for i in range(3)
    ]
    assert rerank_history_hits("query", hits, Decider(HaltBackend("turn_1")), limit=1) == hits
