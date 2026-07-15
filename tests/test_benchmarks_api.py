"""Tests for the benchmark report: the snapshot builder + the shipped-JSON loader.

Load-bearing properties: ``build_snapshot`` reads the committed local_lift paired result into an
internal-lift block (n>0) that is honestly flagged NOT significant, with a non-empty external list;
``benchmark_report`` loads the shipped snapshot (``available == True`` on this repo); and a
missing/malformed snapshot degrades to ``available == False`` (never a raise, never a 500).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.api.benchmarks_api import benchmark_report
from chimera.eval.benchmark_snapshot import build_snapshot


def test_build_snapshot_internal_lift_and_external() -> None:
    snap = build_snapshot()

    lift = snap["internal_lift"]
    assert lift["n"] > 0
    # The internal lift is PROMISING but not yet significant — the caveat travels with the number.
    assert lift["significant"] is False
    assert 0.0 <= lift["baseline_rate"] <= 1.0
    assert 0.0 <= lift["treatment_rate"] <= 1.0
    assert len(lift["ci"]) == 2 and lift["ci"][0] <= lift["ci"][1]
    assert lift["source"] == "bench/local_lift/results/paired.json"
    assert "discordant" in lift and set(lift["discordant"]) == {"treatment_only", "baseline_only"}

    # The humbling external result is published alongside — the list is non-empty and also not significant.
    assert snap["external"], "the external Terminal-Bench block must be present"
    ext = snap["external"][0]
    assert ext["significant"] is False
    assert ext["source"] == "bench/terminal_bench/RESULTS.md"
    assert isinstance(snap["generated_for"], str)


def test_benchmark_report_available_on_this_repo() -> None:
    report = benchmark_report()

    # The snapshot ships inside the package, so the report is available here.
    assert report["available"] is True
    assert report["internal_lift"] is not None
    assert report["internal_lift"]["significant"] is False
    assert report["external"]
    assert isinstance(report["generated_for"], str)


def test_benchmark_report_reads_a_written_snapshot(tmp_path: Path) -> None:
    snap_file = tmp_path / "_benchmark_snapshot.json"
    snap_file.write_text(json.dumps(build_snapshot()), encoding="utf-8")

    report = benchmark_report(snapshot_file=snap_file)
    assert report["available"] is True
    assert report["internal_lift"]["n"] > 0


def test_benchmark_report_unavailable_when_missing(tmp_path: Path) -> None:
    report: dict[str, Any] = benchmark_report(snapshot_file=tmp_path / "does_not_exist.json")
    assert report["available"] is False
    assert report["internal_lift"] is None
    assert report["external"] == []
    assert report["generated_for"] is None


def test_benchmark_report_unavailable_when_malformed(tmp_path: Path) -> None:
    bad = tmp_path / "_benchmark_snapshot.json"
    bad.write_text("[]", encoding="utf-8")  # a list, not the expected benchmark object

    report = benchmark_report(snapshot_file=bad)
    assert report["available"] is False
    assert report["internal_lift"] is None
    assert report["external"] == []
