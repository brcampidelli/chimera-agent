"""Regression coverage for the P1-06 measurement and spend safeguards."""
from __future__ import annotations

import json
from pathlib import Path

from bench.design_refine import report


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_historical_receipts_reproduce_corrected_first_answers_and_reads() -> None:
    results = Path(__file__).resolve().parents[1] / "bench" / "design_refine" / "results"
    for model, n, a, b, named, ordinal in (
        ("ds", 90, 18, 21, (0, 60), (1, 30)),
        ("glm", 81, 18, 11, (0, 57), (15, 24)),
    ):
        r = report.model_report(model, results)
        assert r["refine_success"]["n"] == n
        assert round(r["refine_success"]["A"] * n) == a
        assert round(r["refine_success"]["B"] * n) == b
        assert (r["read_share_note"]["named"]["read_rounds"], r["read_share_note"]["named"]["n"]) == named
        assert (r["read_share_note"]["ordinal"]["read_rounds"], r["read_share_note"]["ordinal"]["n"]) == ordinal
    assert not report.gates(report.model_report("glm", results))["gate1"]


def test_missing_measurement_and_halted_pairs_cannot_pass_gates_or_enter_totals(tmp_path: Path) -> None:
    root = tmp_path / "x"
    shape = {"edits": 1, "whole": 0, "collateral": 0}
    def row(brief: str, arm: str, halt: bool, prompt: int) -> dict:
        return {"brief": brief, "rep": 0, "arm": arm, "halt": halt, "screens": 3, "how": "named",
                "shape": shape, "ok": True, "calls": 1, "prompt": prompt, "read_rounds": 0,
                "usd": 0.0, "usd_live": 0.0}
    _write(root / "refine.jsonl", [row("good", "A", False, 100), row("good", "B", False, 40),
                                   row("halted", "A", False, 900), row("halted", "B", True, 100)])
    _write(root / "gen.jsonl", [{"brief": "g", "rep": 0, "arm": arm, "halt": False,
                                 "ok": True, "attempts": 1, "usd": 0, "usd_live": 0} for arm in "AB"])
    r = report.model_report("x", tmp_path)
    assert r["tokens"]["all"]["A"] == 100
    assert r["tokens"]["all"]["B"] == 40
    assert r["refine_success"]["n"] == 1
    assert r["gen_contrast"]["n"] == r["gen_overflow"]["n"] == 0
    assert not report.gates(r)["gate3"]


def test_historical_perturbations_are_reported_from_the_paired_receipts() -> None:
    results = Path(__file__).resolve().parents[1] / "bench" / "design_refine" / "results"
    ds, glm = (report.model_report(model, results) for model in ("ds", "glm"))
    assert round(ds["screen_success"]["4"]["diff_pp"], 2) == -13.33
    assert round(glm["screen_success"]["5"]["diff_pp"], 2) == -18.52
    assert round(glm["order_success"]["A"]["diff_pp"], 2) == -15.00
    assert round(glm["order_success"]["B"]["diff_pp"], 2) == -2.44
    assert ds["measurement_coverage"]["overflow_missing"] == 0
    assert glm["measurement_coverage"]["contrast_missing"] == 0


def test_interrupted_measurements_fail_gate_but_explicit_contrast_refusal_is_distinct(tmp_path: Path) -> None:
    root = tmp_path / "x"
    gen = [{"brief": f"g{i}", "rep": 0, "arm": arm, "halt": False, "ok": True,
            "attempts": 1, "usd": 0, "usd_live": 0} for i in range(2) for arm in "AB"]
    _write(root / "gen.jsonl", gen)
    measured = [{"brief": r["brief"], "rep": 0, "arm": r["arm"], "overflow": False,
                 "contrast": {"measured": True, "low": 0}} for r in gen]
    measured[2]["contrast"] = {"measured": False, "low": 0}
    _write(root / "gen_measured.jsonl", measured[:-1])
    partial = report.model_report("x", tmp_path)
    assert partial["measurement_coverage"] == {"valid_artboards": 4, "overflow_missing": 1,
                                                "contrast_missing": 1, "contrast_refused": 1}
    assert partial["gen_overflow"]["n"] == 1
    assert not report.gates(partial)["gate3"]

    _write(root / "gen_measured.jsonl", measured)
    complete = report.model_report("x", tmp_path)
    assert complete["measurement_coverage"] == {"valid_artboards": 4, "overflow_missing": 0,
                                                 "contrast_missing": 0, "contrast_refused": 1}
    assert complete["gen_contrast"]["n"] == 1
    assert report.gates(complete)["gate3"]
