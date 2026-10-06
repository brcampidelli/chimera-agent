"""The zero-cost fusion replay emits its registered, auditable readout shape."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "bench" / "fusion_admissibility" / "run.py"


def _runner() -> Any:
    spec = importlib.util.spec_from_file_location("fusion_admissibility_run", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readout_shape_declares_sources_cost_rules_and_admissibility() -> None:
    readout = _runner().replay_readout()

    assert readout["readout_version"] == 1
    assert readout["cost_usd"] == readout["model_calls"] == 0
    assert "bench\\judge_blind_hard\\results\\collect-all.jsonl" in readout["sources"][0]
    assert readout["panel_size"] == 3 and readout["probe_size"] == 2
    assert readout["hard_member_admissibility"]["n_items"] == 50
    assert len(readout["hard_member_admissibility"]["pairs"]) == 3
    assert readout["hard_published_icc1"] == 0.5266
    rules = readout["hard_agreement_rules"]
    assert set(rules) >= {"phrasing", "answer_equal", "missing_or_unextractable_answers_in_probes"}
    assert rules["phrasing"]["items"] == rules["answer_equal"]["items"] == 50
    assert rules["answer_equal"]["estimated_member_calls_saved_best_of_3"] == 12
    assert "aggregate_member_admissibility" in readout
