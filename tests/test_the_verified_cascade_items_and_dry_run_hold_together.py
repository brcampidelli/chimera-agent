"""The frozen items of the verified-cascade bench, and the whole harness end to end on a fake backend.

Both need ``bench/verified_cascade/results/``, which the WSL gate does not copy, so they skip cleanly
where it is absent. Where it is present:

* the validator passes and the freeze is reproducible byte for byte (Amendment 0);
* the dry run goes through S0–S6 with every gate passed, spends under the admission stop, and a second
  run makes **no new call** (resumption);
* the report stays **blind** until S3 is complete, and then produces the registered verdict fields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

RESULTS = REPO / "bench" / "verified_cascade" / "results"
needs_results = pytest.mark.skipif(
    not (RESULTS / "items.jsonl").exists(), reason="bench/verified_cascade/results is not in this tree"
)


@needs_results
def test_the_validator_passes_and_the_freeze_is_reproducible() -> None:
    from bench.verified_cascade import check_items
    from bench.verified_cascade.freeze import freeze

    assert check_items.full() == []
    assert freeze(check=True) == 0


@needs_results
def test_the_dry_run_goes_end_to_end_resumes_and_reports(tmp_path: Path) -> None:
    from bench.verified_cascade import report, run

    out = tmp_path / "dry"
    assert run.main(["--dry-run", "--out", str(out)]) == 0
    gates = json.loads((out / "gates.json").read_text(encoding="utf-8"))
    assert set(gates) == set(run.STAGES) and all(g["passed"] for g in gates.values())
    lines = (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    spent = sum(float(json.loads(x).get("usd") or 0.0) for x in lines)
    assert 0 < spent < 18.0
    assert gates["s6"]["f1_coverage"] == gates["s6"]["n"], "Amendment 0: C covers every item"

    assert run.main(["--dry-run", "--out", str(out)]) == 0
    assert len((out / "calls.jsonl").read_text(encoding="utf-8").splitlines()) == len(lines), "a resumed run paid again"

    report.REPS = 200
    rep = report.build(out)
    assert not rep["blind"]
    assert set(rep["verdict"]) == {"B", "D"}
    for key in ("B-A", "D-A", "L-A", "C-A", "B-C"):
        p = rep["paired"][key]
        assert p["n"] > 0 and 0.0 <= p["p"] <= 1.0 and p["newcombe"][0] <= p["diff"] <= p["newcombe"][1]
    assert rep["mechanics"]["fuse_reason"] == {"length": len(run.RunData(out).items)}


@needs_results
def test_the_report_is_blind_before_s3(tmp_path: Path) -> None:
    from bench.verified_cascade import report, run

    out = tmp_path / "pilot"
    for stage in ("s0", "s1", "s2"):
        assert run.main(["--dry-run", "--stage", stage, "--out", str(out)]) == 0
    rep = report.build(out)
    assert rep["blind"] and "arms" not in rep and "paired" not in rep
    assert "BLIND" in report.fmt(rep)


@needs_results
def test_a_stage_refuses_to_start_before_its_gate(tmp_path: Path) -> None:
    from bench.verified_cascade import run

    assert run.main(["--dry-run", "--stage", "s3", "--out", str(tmp_path / "early")]) == 2
