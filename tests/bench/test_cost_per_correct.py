"""The cost reanalysis must reproduce the figures the benches PUBLISHED, not its own output.

Every expected number below is printed in a committed file, cited beside it. The first version of this
test hard-coded the script's own output (D "149 correct of 398" beside a RESULTS.md that says D ships 21
wrong answers), so it could only ever confirm itself. ``mismatches`` is a pure function so the sabotage
tests can prove the comparison bites.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bench/cost_per_correct/reanalyze.py"
SPEC = importlib.util.spec_from_file_location("cost_per_correct_reanalyze", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
reanalyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reanalyze)

J, LUNA, CLEF, INTERN = reanalyze.GOVERNANCE_FILES

# bench/verified_cascade/RESULTS.md (verdict table, "Hand-offs") and results/run/report.txt (arm table):
# (n with a label, wrong shipped, hand-offs)
CASCADE_PUBLISHED = {
    "A": (400, 33, 0),
    "B": (399, 21, 210),
    "B_decl": (399, 21, 38),
    "C": (384, 20, 0),
    "D": (398, 21, 225),
    "D_decl": (398, 21, 16),
    "L": (389, 22, 0),
}
# bench/jev_decisions/RESULTS.md §2 (J pooled: verdict catch 24/24, verdict FR 11/31) and §5 (OATS 64/64);
# RESULTS-luna-clef.md (OATS verdict catch 62/64 and 64/64; halts 1 and 0; cost per request);
# intern_decision_local/report-governance-registered.md (pooled 20/24, 5/31; OATS 63/64).
GOVERNANCE_PUBLISHED: dict[str, dict[str, Any]] = {
    J: {"catch": (24, 24), "false_refuse": (11, 31), "oats": (64, 64), "per_request": "0.000023"},
    LUNA: {"oats": (62, 64), "halts": 1, "per_request": "0.000047"},
    CLEF: {"oats": (64, 64), "halts": 0, "per_request": "0.000036"},
    INTERN: {"catch": (20, 24), "false_refuse": (5, 31), "oats": (63, 64), "halts": 0},
}
# intern_decision_local/RESULTS.md §A
JEVBENCH_PUBLISHED = {"all": (201, 231), "easy": (48, 48), "original": (71, 72), "hard": (82, 111)}


def mismatches(cascade: dict[str, dict[str, Any]], gov: dict[str, dict[str, Any]], jb: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    for arm, (n, wrong, handoffs) in CASCADE_PUBLISHED.items():
        got = (cascade[arm]["n"], cascade[arm]["wrong"], cascade[arm]["handoffs"])
        if got != (n, wrong, handoffs):
            bad.append(f"cascade {arm}: (n, wrong, hand-offs) {got} != published {(n, wrong, handoffs)}")
        if cascade[arm]["n"] + cascade[arm]["missing"] != 400:
            bad.append(f"cascade {arm}: n + missing != 400")
    for arm, exp in GOVERNANCE_PUBLISHED.items():
        m = gov[arm]
        pairs = {"catch": (m["catch"], m["attacks"]), "false_refuse": (m["false_refuse"], m["benign"]),
                 "oats": (m["oats_catch"], m["oats"])}
        for key, value in exp.items():
            if key in pairs and pairs[key] != value:
                bad.append(f"{arm} {key}: {pairs[key]} != published {value}")
        if "halts" in exp and m["halts_file"] != exp["halts"]:
            bad.append(f"{arm} halts: {m['halts_file']} != published {exp['halts']}")
        if "per_request" in exp and f"{m['per_request_file']:.6f}" != exp["per_request"]:
            bad.append(f"{arm} cost per request {m['per_request_file']:.6f} != published {exp['per_request']}")
        if m["n"] != 119 or m["correct"] != m["catch"] + (m["benign"] - m["false_refuse"]) + m["oats_catch"]:
            bad.append(f"{arm}: slice is not the 119 comparable requests, or correct does not decompose")
    tiers = {**jb["tiers"], "all": (jb["correct"], jb["n"])}
    for tier, value in JEVBENCH_PUBLISHED.items():
        if tiers[tier] != value:
            bad.append(f"JevBench {tier}: {tiers[tier]} != published {value}")
    return bad


@pytest.fixture(scope="module")
def results() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    # The reanalysis reads committed results files. A copy of the tree made without them (the WSL gate
    # rsyncs with `--exclude 'bench/*/results*'`) cannot run it, and says so instead of erroring.
    if not (ROOT / "bench/verified_cascade/results/run/report.json").is_file():
        pytest.skip("bench results are not in this checkout; the reanalysis reads them")
    return reanalyze.cascade_results(), reanalyze.governance_results(), reanalyze.jevbench_result()


def test_reproduces_every_published_figure(results: tuple[Any, Any, Any]) -> None:
    assert mismatches(*results) == []


def test_cascade_correct_and_cost_match_the_bench_report(results: tuple[Any, Any, Any]) -> None:
    """report.json (committed) carries report.py's own mean_cost and cost_per_correct for the primary arms."""
    cascade = results[0]
    arms = json.loads((ROOT / "bench/verified_cascade/results/run/report.json").read_text(encoding="utf-8"))["arms"]
    for arm in ("A", "B", "C", "D", "L"):
        m, pub = cascade[arm], arms[arm]
        assert abs(m["cost"] - pub["mean_cost"] * pub["n"]) < 1e-9, arm
        assert abs(m["cost"] / m["correct"] - pub["cost_per_correct"]) < 1e-12, arm


def test_shipped_decline_is_graded_not_assumed(results: tuple[Any, Any, Any]) -> None:
    """D_decl ships 209 declines; d1 graded 202 correct and 7 wrong. Counting every decline as correct
    would give 358, the rows report.py labels only ``decline``."""
    d = results[0]["D_decl"]
    assert (d["declines_shipped"], d["declines_graded_wrong"], d["correct"]) == (209, 7, 351)


@pytest.mark.parametrize("mutate", [
    lambda c, g, j: c["D"].__setitem__("wrong", 20),
    lambda c, g, j: c["A"].__setitem__("n", 399),
    lambda c, g, j: g[J].__setitem__("false_refuse", 10),
    lambda c, g, j: g[LUNA].__setitem__("oats_catch", 63),
    lambda c, g, j: g[CLEF].__setitem__("per_request_file", 0.00004),
    lambda c, g, j: j["tiers"].__setitem__("hard", (81, 111)),
])
def test_comparison_bites_on_a_single_changed_count(results: tuple[Any, Any, Any], mutate: Any) -> None:
    sabotaged = copy.deepcopy(results)
    mutate(*sabotaged)
    assert mismatches(*sabotaged), "a changed count went unnoticed"


def test_zero_cost_is_explicitly_local() -> None:
    assert reanalyze.per_thousand(0.0, 109) == "0 (local)"


def test_verdict_mapping_follows_report_py() -> None:
    assert reanalyze.verdict_correct({"verdict": "REVIEW", "label": "attack"})
    assert reanalyze.verdict_correct({"verdict": "ALLOW", "label": "benign"})
    assert not reanalyze.verdict_correct({"verdict": "ALLOW", "label": "attack"})
    assert not reanalyze.verdict_correct({"verdict": "BLOCK", "label": "benign"})
