"""Tests for BM25 skill-card retrieval and injection (no network)."""

from __future__ import annotations

from pathlib import Path

from chimera.core import AutonomousAgent, AutonomousConfig
from chimera.core.agent import AgentResult
from chimera.evolution import CardIndex, CardRetriever, LearnedSkill, SkillStore
from chimera.evolution.card_retrieval import cards_context_block


def _cards() -> list[LearnedSkill]:
    return [
        LearnedSkill(
            name="two_pointer",
            description="two-pointer scan of a sorted array",
            do="move pointers inward",
            check="pointers do not cross",
            triggers=["sorted", "pair", "two pointer", "target"],
        ),
        LearnedSkill(
            name="dynamic_programming",
            description="tabulate overlapping subproblems",
            do="fill a table bottom-up",
            check="base cases set",
            triggers=["dp", "subproblem", "memoize", "table"],
        ),
    ]


def test_card_index_ranks_matching_triggers_first() -> None:
    index = CardIndex(_cards())
    top = index.search("find a pair in a sorted array summing to a target", k=1)
    assert len(top) == 1
    assert top[0].name == "two_pointer"


def test_card_index_empty_query_returns_nothing() -> None:
    assert CardIndex(_cards()).search("", k=3) == []


def test_context_block_has_template_and_instruction() -> None:
    block = cards_context_block(_cards()[:1])
    assert "Retrieved reasoning skills:" in block
    assert "[two_pointer]" in block
    assert "- Do: move pointers inward" in block
    assert "Prefer the most directly applicable skill" in block


def test_context_block_tags_anti_pattern() -> None:
    card = LearnedSkill(name="bad", description="d", kind="anti_pattern", do="x", check="y")
    assert "(anti-pattern)" in cards_context_block([card])


def test_retriever_from_store(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    for card in _cards():
        store.add(card)
    ctx = CardRetriever(store, k=1).card_context("two pointer on a sorted array")
    assert "[two_pointer]" in ctx


def test_retriever_empty_store_returns_blank(tmp_path: Path) -> None:
    assert CardRetriever(SkillStore(tmp_path / "skills.json")).card_context("anything") == ""


class _CapturingWorker:
    """Records the prompt it is asked to run."""

    def __init__(self) -> None:
        self.prompt = ""

    def run(self, task: str) -> AgentResult:
        self.prompt = task
        return AgentResult(answer="done", steps=1, stopped_reason="final", transcript=[])


class _FixedCards:
    def card_context(self, task: str) -> str:
        return "Retrieved reasoning skills:\n[sentinel_card]\n- Do: the thing"


def test_autonomous_injects_card_context() -> None:
    worker = _CapturingWorker()
    auto = AutonomousAgent(
        worker, cards=_FixedCards(), config=AutonomousConfig(use_planner=False, use_manager=False)
    )
    auto.run("solve something")
    assert "sentinel_card" in worker.prompt  # the card block reached the worker's context


def test_card_index_is_a_closing_context_manager() -> None:
    import sqlite3

    from chimera.evolution.card_retrieval import CardIndex

    with CardIndex([]) as index:
        conn = index._conn
    # after the context exits the in-memory connection is closed (no per-retrieval leak)
    try:
        conn.execute("SELECT 1")
        closed = False
    except sqlite3.ProgrammingError:
        closed = True
    assert closed is True
