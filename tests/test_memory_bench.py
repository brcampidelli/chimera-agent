"""Tests for the memory-bench (M11a) — recall@k, lexical vs paraphrase, scale."""

from __future__ import annotations

from pathlib import Path

from chimera.eval import memory_sweep, run_memory_bench, synthetic_facts_and_probes
from chimera.eval.memory_bench import _SYNONYMS
from chimera.memory import MemoryManager, MemoryStore


def _manager(tmp_path: Path, name: str = "m.json") -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / name))


def test_corpus_shape() -> None:
    facts, probes = synthetic_facts_and_probes(50)
    assert len(facts) == 50  # 16 targets + 34 distractors
    targets = [f for f in facts if f[0].startswith("target:")]
    assert len(targets) == len(_SYNONYMS)
    # each target has a lexical + a paraphrase probe
    assert len(probes) == 2 * len(_SYNONYMS)
    assert {p.kind for p in probes} == {"lexical", "paraphrase"}


def test_lexical_recall_high_paraphrase_low_for_keyword_search(tmp_path: Path) -> None:
    facts, probes = synthetic_facts_and_probes(200)
    report = run_memory_bench(_manager(tmp_path), facts, probes, k=5)
    s = report.summary()
    # Keyword search finds the exact needle token every time...
    assert s["recall@k_lexical"] == 1.0
    # ...but has no bridge to a synonym — the honest ceiling this bench exists to show.
    assert s["recall@k_paraphrase"] == 0.0


def test_lexical_recall_holds_at_scale(tmp_path: Path) -> None:
    reports = memory_sweep(lambda: _manager(tmp_path, f"m{id(object()):x}.json"), [50, 1000])
    for report in reports:
        assert report.summary()["recall@k_lexical"] == 1.0  # exact-token recall survives noise


def test_recall_at_1_subset_of_recall_at_k(tmp_path: Path) -> None:
    facts, probes = synthetic_facts_and_probes(50)
    s = run_memory_bench(_manager(tmp_path), facts, probes, k=5).summary()
    assert s["recall@1"] <= s["recall@k"]


def test_small_corpus_uses_available_synonyms(tmp_path: Path) -> None:
    facts, probes = synthetic_facts_and_probes(3)
    assert len(facts) == 3 and len(probes) == 6  # 3 targets, no distractors


def test_summary_keys(tmp_path: Path) -> None:
    facts, probes = synthetic_facts_and_probes(20)
    s = run_memory_bench(_manager(tmp_path), facts, probes).summary()
    for key in ("n_facts", "probes", "recall@1", "recall@k", "recall@k_lexical", "recall@k_paraphrase"):
        assert key in s
