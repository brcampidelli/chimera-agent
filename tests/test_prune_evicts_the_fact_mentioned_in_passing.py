"""`prune` evicts a short fact written early, whatever else is true about it — pinned, not fixed.

`bench/memory_prune` (2026-09-11, deterministic, 288 cells): at a 20% budget the fact *"the customer
is allergic to penicillin"* survives in 7 of 144 cells, all of them *written last, semantic, keyed*.
A keyed, user-written, semantic fact written FIRST is evicted 0/6. arXiv 2609.05767 measured the
same class of policy at 0–1% survival and named the cause: a budget decided before the question is
known cannot rank the fact that the question will need.

This test asserts the finding as the code stands, so a change to `chimera/memory/value.py` that
moves it has to update this file and say what it traded. The mechanism that keeps a fact through any
budget is `persona`, which `prune` never touches — asserted here too, because it is the instruction
the finding comes with.
"""

from __future__ import annotations

from pathlib import Path

from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore

FACT = "the customer is allergic to penicillin"


def _store(tmp_path: Path) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "mem.json"))


def _fill(mgr: MemoryManager, n: int) -> None:
    for i in range(n):
        # Ordinary working memory: longer than the fact, mixed kinds, some keyed, some user-written.
        mgr.remember(
            f"note {i}: " + "release index queue tenant invoice retry schema cache " * (2 + i % 5),
            "semantic" if i % 3 else "episodic",
            key=f"k{i}" if i % 2 else None,
            source="user" if i % 4 == 0 else "chimera",
        )


def test_the_fact_written_first_is_evicted_at_a_twenty_percent_budget_even_when_curated(tmp_path: Path) -> None:
    mgr = _store(tmp_path)
    mgr.remember(FACT, "semantic", key="allergy", source="user")
    _fill(mgr, 50)
    mgr.prune(int(round(51 * 0.2)))
    assert not any(item.content == FACT for item in mgr.store.all()), (
        "the fact survived — the value model changed; update bench/memory_prune and this test"
    )


def test_the_same_fact_written_last_survives(tmp_path: Path) -> None:
    mgr = _store(tmp_path)
    _fill(mgr, 50)
    mgr.remember(FACT, "semantic", key="allergy", source="user")
    mgr.prune(int(round(51 * 0.2)))
    assert any(item.content == FACT for item in mgr.store.all())


def test_persona_is_the_one_profile_a_budget_cannot_evict(tmp_path: Path) -> None:
    mgr = _store(tmp_path)
    mgr.remember(FACT, "persona")
    _fill(mgr, 50)
    mgr.prune(int(round(51 * 0.2)))
    assert any(item.content == FACT for item in mgr.store.all())
