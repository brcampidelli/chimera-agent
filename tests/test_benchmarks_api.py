"""Tests for the benchmark report: the snapshot builder + the shipped-JSON loader.

Load-bearing properties: ``build_snapshot`` reads the local_lift paired result into an internal-lift
block (n>0) whose significance flag is consistent with its own CI, with a non-empty external list;
``benchmark_report`` loads the shipped snapshot (``available == True`` on this repo); and a
missing/malformed snapshot degrades to ``available == False`` (never a raise, never a 500).

Two things these tests deliberately do NOT assume:

- ``bench/local_lift/results/paired.json`` is **gitignored**, so it exists only after a local run.
  Anything reading it skips on a fresh clone — a missing local artefact is not a defect. What ships
  inside the package (the snapshot JSON) stays unconditional, so CI still guards published numbers.
- The significance verdict is not hardcoded. It is an empirical fact about the current recorded run
  (False at n=15, True at n=100), so pinning a literal makes the suite fail whenever an honest re-run
  moves the number. What must hold is that the flag never contradicts the interval it ships beside.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.api.benchmarks_api import benchmark_report
from chimera.eval.benchmark_snapshot import _default_paired_path, build_snapshot

_needs_paired = pytest.mark.skipif(
    not _default_paired_path().exists(),
    reason="bench/local_lift/results/paired.json is gitignored — present only after a local run",
)


@_needs_paired
def test_build_snapshot_internal_lift_and_external() -> None:
    snap = build_snapshot()

    lift = snap["internal_lift"]
    assert lift["n"] > 0
    # The verdict must agree with the interval it travels with: significant iff the 95% paired CI
    # excludes 0. Asserting the relationship instead of the literal keeps the caveat enforced without
    # freezing a number that an honest re-run is expected to move.
    lo, hi = lift["ci"]
    assert lift["significant"] is (lo > 0.0 or hi < 0.0)
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


@_needs_paired
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
