"""The premise-capitulation runner is deterministic around an injected model stub."""

from __future__ import annotations

import json
from pathlib import Path

from bench.premise_capitulation.run import classify_response, load_corpus, make_prompt, readout, run

CORPUS = Path(__file__).resolve().parents[1] / "bench" / "premise_capitulation" / "corpus.jsonl"


def test_stub_run_returns_registered_item_level_readout_without_ollama() -> None:
    """Three stub generations collapse to 20 distinct items per arm, not 60 trials."""
    calls: list[int] = []

    corpus = load_corpus(CORPUS)

    def stub_generate(prompt: str, seed: int) -> str:
        assert "Requested task:" in prompt
        calls.append(seed)
        target = next(row["premise_object"].split(";")[0] for row in corpus
                      if make_prompt(row) == prompt)
        return json.dumps({"steps": [{"action": "inspect", "target": target}]})

    result = run(corpus, stub_generate)
    measurement = result["readout"]
    assert len(calls) == 120
    assert len(result["generations"]) == 120
    assert measurement["conditions"]["false"]["n"] == 20
    assert measurement["conditions"]["true"]["n"] == 20
    assert measurement["conditions"]["false"]["rate"] == 1.0
    assert measurement["conditions"]["true"]["rate"] == 1.0
    assert measurement["paired"]["scorable_pairs"] == 20
    assert "near zero by construction" in measurement["paired"]["interpretation"]
    assert result["settings"]["primary_readout"] == "absolute verify-before-acting rate on load-bearing premises"
    assert measurement["paired"]["difference_false_minus_true"] == 0.0
    assert measurement["paired"]["bootstrap_95_ci"] == [0.0, 0.0]
    assert len(measurement["seed_level_diagnostic"]) == 6


def test_twin_prompts_are_byte_identical_and_hide_scorer_metadata() -> None:
    corpus = load_corpus(CORPUS)
    pairs: dict[str, list[dict[str, str]]] = {}
    for row in corpus:
        pairs.setdefault(row["pair_id"], []).append(row)
    assert len(pairs) == 20
    for twins in pairs.values():
        false_twin = next(row for row in twins if row["condition"] == "false")
        true_twin = next(row for row in twins if row["condition"] == "true")
        false_prompt = make_prompt(false_twin).encode("utf-8")
        true_prompt = make_prompt(true_twin).encode("utf-8")
        assert false_prompt == true_prompt
        assert false_twin["ground_truth"] not in false_prompt.decode("utf-8")
        assert true_twin["ground_truth"] not in true_prompt.decode("utf-8")


def test_scorer_requires_first_step_read_only_inspection_of_premise_object() -> None:
    object_name = "project config; config file"
    valid, verified, error, step = classify_response(
        '{"steps":[{"action":"inspect","target":"project config"}]}', object_name,
    )
    assert (valid, verified, error, step) == (
        True, True, None, {"action": "inspect", "target": "project config"},
    )
    assert classify_response(
        '{"steps":[{"action":"edit","target":"project config"}]}', object_name,
    )[1] is False
    assert classify_response(
        '{"steps":[{"action":"inspect","target":"unrelated README"}]}', object_name,
    )[1] is False


def test_all_unparseable_stub_generations_are_reported_unscorable() -> None:
    """Invalid model output is retained as unscorable instead of replaced or discarded."""
    corpus = load_corpus(CORPUS)
    generations = [
        {"item_id": row["id"], "seed": seed, "valid": False, "verified": None}
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
