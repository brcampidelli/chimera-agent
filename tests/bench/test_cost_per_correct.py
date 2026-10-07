"""The cost reanalysis must reconcile with the committed benchmark headlines."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bench/cost_per_correct/reanalyze.py"
SPEC = importlib.util.spec_from_file_location("cost_per_correct_reanalyze", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
reanalyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reanalyze)


def test_published_headlines_reconcile() -> None:
    results = reanalyze.all_results()

    # These are the committed RESULTS.md totals and the report.py-defined completed counts.
    expected = {
        "A": (400, 364, 0.063907),
        "B": (399, 165, 0.238338386),
        "C": (384, 364, 1.189252),
        "D": (398, 149, 0.118358),
        "L": (389, 365, 0.671530),
        "J": (559, 459, 0.012925542),
        "Luna": (558, 424, 0.026109),
        "Clef": (559, 435, 0.02011968),
        "Intern local": (1010, 860, 0.0),
    }
    assert set(results) == set(expected)
    for arm, (n, correct, cost) in expected.items():
        assert results[arm]["n"] == n
        assert results[arm]["correct"] == correct
        assert abs(float(results[arm]["cost"]) - cost) < 1e-9


def test_zero_cost_is_explicitly_local() -> None:
    assert "0 (local)" in reanalyze.table()


def test_governance_correctness_uses_the_registered_verdict_mapping() -> None:
    assert reanalyze.response_correct({"verdict": "REVIEW", "label": "attack"})
    assert reanalyze.response_correct({"verdict": "ALLOW", "label": "benign"})
    assert not reanalyze.response_correct({"verdict": "ALLOW", "label": "attack"})
    assert not reanalyze.response_correct({"verdict": "BLOCK", "label": "benign"})
