"""The S30-61 local replication's injector: each registered mutation is caught, a clean copy is not."""
from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

from chimera.governance.reconcile import append_wire_record, digest

ROOT = Path(__file__).parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("wire_ollama", ROOT / "bench/wire_reconcile/run_ollama.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pristine_run(run_dir: Path, steps: int) -> None:
    home = run_dir / "home"
    home.mkdir(parents=True)
    rows = []
    for index in range(steps):
        request, response = digest({"q": index}), digest({"a": index})
        wire_id = append_wire_record(home / "wire.jsonl", model="m", request_digest=request, response_digest=response)
        rows.append({"index": index, "wire_id": wire_id, "request_digest": request, "response_digest": response})
    (home / "traces.jsonl").write_text(json.dumps({"run_id": "x", "steps": rows}) + "\n", encoding="utf-8")


def test_every_registered_mutation_is_detected_and_clean_copies_are_not(tmp_path: Path) -> None:
    runner = _load()
    runs = tmp_path / "runs"
    for number, steps in enumerate((1, 2, 4)):
        _pristine_run(runs / f"t{number:02d}-r00", steps)
    (runs / "t09-r00" / "home").mkdir(parents=True)  # a run that produced nothing
    result = runner.inject(runs, tmp_path / "mutated")
    assert result["counts"] == {"clean": [0, 3], "omission": [3, 3], "fabrication": [3, 3], "altered copy": [3, 3]}
    assert result["protocol_failures"] == ["t09-r00"]
    assert result["clean_false_positives"] == []


def test_a_gateway_call_that_is_not_a_step_is_a_clean_false_positive(tmp_path: Path) -> None:
    runner = _load()
    runs = tmp_path / "runs"
    _pristine_run(runs / "t00-r00", 2)
    # e.g. a retry or a compaction summary: the gateway saw it, the steplog never had a step for it.
    append_wire_record(runs / "t00-r00" / "home" / "wire.jsonl", model="m", request_digest="r", response_digest="s")
    result = runner.inject(runs, tmp_path / "mutated")
    assert result["counts"]["clean"] == [1, 1]
    assert len(result["clean_false_positives"][0]["missing_steplog"]) == 1


def test_fabrication_is_well_formed_not_caught_by_a_missing_field() -> None:
    runner = _load()
    steps = [{"index": 0, "wire_id": "a" * 32, "request_digest": "r", "response_digest": "s"}]
    mutated = runner.mutate(steps, "fabrication", random.Random(1))
    added = [s for s in mutated if s["wire_id"] != "a" * 32]
    assert len(added) == 1 and len(added[0]["wire_id"]) == 32
    assert len(added[0]["request_digest"]) == len(added[0]["response_digest"]) == 64
    assert steps == [{"index": 0, "wire_id": "a" * 32, "request_digest": "r", "response_digest": "s"}]
