"""No-network test of the registered candidate-visibility replay apparatus."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def _runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "fusion_synth_candidates_runner", REPO / "bench/fusion_synth_candidates/run.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_seed_headroom_selection_and_paired_fake_replay() -> None:
    run = _runner()
    row = {"item_id": "aime-x", "question": "Find 42.", "reference": "42",
           "answers": ["ANSWER: 42", "ANSWER: 7", ""],
           "writers": ["writer-a", "writer-b", "writer-c"],
           "correct": [True, False, False]}
    excluded = {**row, "item_id": "aime-y", "correct": [True, True, True]}
    rows, exclusion_count = run.cohort([row, excluded])
    assert [r["item_id"] for r in rows] == ["aime-x"]
    assert exclusion_count == 1

    prompts = run.prompts(row)
    assert "Candidate answers:" not in prompts["A_as_sent"]
    assert "ANSWER: 7" not in prompts["A_as_sent"]
    assert "ANSWER: 42" in prompts["B_candidates_visible"]
    assert "writer-b" in prompts["B_candidates_visible"]

    calls: list[str] = []
    def fake(prompt: str) -> str:
        calls.append(prompt)
        return "ANSWER: 7" if "Candidate answers:" not in prompt else "ANSWER: 42"

    result = run.replay(rows, fake, model="fake")
    assert len(calls) == 2
    item = result[0]
    assert item["arms"]["A_as_sent"]["correct"] is False
    assert item["arms"]["B_candidates_visible"]["correct"] is True
    assert item["best_candidate_correct"] is True
    assert run.summary(result)["mcnemar_discordant_A_wrong_B_right"] == 1


def test_live_runner_refuses_existing_output_and_never_calls_overwrite(tmp_path: Path) -> None:
    run = _runner()
    output = tmp_path / "results.jsonl"
    output.write_text("keep", encoding="utf-8")
    try:
        run.run("http://localhost:11434/v1", "qwen3:4b", output,
                call=lambda _: (_ for _ in ()).throw(AssertionError("call must not happen")))
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing output was overwritten")
    assert output.read_text(encoding="utf-8") == "keep"


def test_extraction_matches_seed_convention() -> None:
    run = _runner()
    assert run.extract("reasoning\nANSWER: 1,234") == "1234"
    assert run.extract("nothing numeric") is None


def test_preregistration_records_the_exact_live_command() -> None:
    prereg = (REPO / "bench/fusion_synth_candidates/PREREGISTRATION.md").read_text(encoding="utf-8")
    assert "--model qwen3:4b" in prereg
    assert "--endpoint http://localhost:11434/v1" in prereg
    assert "--output bench/fusion_synth_candidates/results/qwen3-4b.jsonl" in prereg
    source = (REPO / "bench/fusion_synth_candidates/run.py").read_text(encoding="utf-8")
    assert 'output.open("x"' in source
    assert "endpoint must be local" in source


def test_seed_runner_selection_can_load_real_corpus_without_calls() -> None:
    run = _runner()
    rows = [json.loads(line) for line in run.SEED.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected, excluded = run.cohort(rows)
    assert len(rows) == 50
    assert len(selected) == 16
    assert excluded == 34
    assert all(any(row["correct"]) and not all(row["correct"]) for row in selected)
