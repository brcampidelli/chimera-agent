"""The premise-capitulation runner is deterministic around an injected model stub."""

from __future__ import annotations

import json
from pathlib import Path

from bench.premise_capitulation.run import load_corpus, readout, run

CORPUS = Path(__file__).resolve().parents[1] / "bench" / "premise_capitulation" / "corpus.jsonl"


def test_stub_run_returns_registered_item_level_readout_without_ollama() -> None:
    """Three stub generations collapse to 20 distinct items per arm, not 60 trials."""
    calls: list[int] = []

    def stub_generate(prompt: str, seed: int) -> str:
        assert "Requested task:" in prompt
        calls.append(seed)
        return json.dumps({"steps": [{"action": "verify_premise", "target": "check premise"}]})

    corpus = load_corpus(CORPUS)
    result = run(corpus, stub_generate)
    measurement = result["readout"]
    assert len(calls) == 120
    assert len(result["generations"]) == 120
    assert measurement["conditions"]["false"]["n"] == 20
    assert measurement["conditions"]["true"]["n"] == 20
    assert measurement["conditions"]["false"]["rate"] == 1.0
    assert measurement["conditions"]["true"]["rate"] == 1.0
    assert measurement["paired"]["scorable_pairs"] == 20
    assert measurement["paired"]["difference_false_minus_true"] == 0.0
    assert measurement["paired"]["bootstrap_95_ci"] == [0.0, 0.0]
    assert len(measurement["seed_level_diagnostic"]) == 6


def test_all_unparseable_stub_generations_are_reported_unscorable() -> None:
    """Invalid model output is retained as unscorable instead of replaced or discarded."""
    corpus = load_corpus(CORPUS)
    generations = [
        {"item_id": row["id"], "seed": seed, "valid": False, "action": None}
        for row in corpus
        for seed in (31, 32, 33)
    ]
    measurement = readout(corpus, generations)
    assert measurement["conditions"]["false"]["n"] == 0
    assert measurement["conditions"]["false"]["unscorable_items"] == 20
    assert measurement["conditions"]["false"]["rate"] is None
    assert measurement["paired"]["scorable_pairs"] == 0
    assert measurement["paired"]["unscorable_pairs"] == 20
    assert measurement["paired"]["bootstrap_95_ci"] == [None, None]
    assert measurement["seed_level_diagnostic"]["true_seed_31"]["n"] == 0
