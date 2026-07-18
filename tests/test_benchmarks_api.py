"""Tests for the benchmark report: the snapshot builder + the shipped-JSON loader.

Load-bearing properties: ``build_snapshot`` reads the committed local_lift paired result into an
internal-lift block (n>0) whose ``significant`` flag matches the source results file AND is consistent
with its own CI, with a non-empty external list; the SHIPPED snapshot is exactly what the generator
produces (no hand-edits); ``benchmark_report`` loads it (``available == True`` on this repo); and a
missing/malformed snapshot degrades to ``available == False`` (never a raise, never a 500).

The significance verdict is deliberately NOT hardcoded here. It is an empirical fact about the current
recorded run (it was False at n=15 and is True at n=100), so pinning a literal would make the suite
fail whenever an honest re-run changes the number. What must hold is that the flag never drifts from
the evidence: it agrees with ``paired.json`` and with the CI it ships beside.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.api.benchmarks_api import benchmark_report
from chimera.eval.benchmark_snapshot import (
    _default_paired_path,
    build_snapshot,
    snapshot_path,
)

# `bench/local_lift/results/` is gitignored (per-run outputs are not tracked — only the harness and
# the tasks are), so `paired.json` exists ONLY after someone runs the benchmark locally. Anything that
# calls `build_snapshot()` reads it, so those tests must SKIP on a fresh clone rather than fail: a
# missing local artefact is not a defect. Everything checkable from the SHIPPED snapshot (which is
# committed) stays unconditional, so CI still guards the published numbers.
_needs_paired = pytest.mark.skipif(
    not _default_paired_path().exists(),
    reason="bench/local_lift/results/paired.json is gitignored — present only after a local run",
)


@_needs_paired
def test_build_snapshot_internal_lift_and_external() -> None:
    snap = build_snapshot()

    lift = snap["internal_lift"]
    assert lift["n"] > 0

    # The caveat travels with the number: the flag is carried verbatim from the results file...
    paired = json.loads(_default_paired_path().read_text(encoding="utf-8"))
    assert lift["significant"] is paired["summary"]["significant"]
    # ...and it must agree with the CI shipped beside it (significant iff the paired CI excludes 0).
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


@_needs_paired
def test_shipped_snapshot_is_exactly_what_the_generator_produces() -> None:
    """The shipped JSON must be generator output, never a hand-edit.

    Hand-patching ``chimera/_benchmark_snapshot.json`` silently drifts the published prose/numbers away
    from ``python -m chimera.eval.benchmark_snapshot``; this pins the two together. ``generated_for`` is
    excluded so a version bump before the release-time regenerate isn't a false failure.
    """
    shipped = json.loads(snapshot_path().read_text(encoding="utf-8"))
    generated = build_snapshot()

    assert isinstance(shipped["generated_for"], str)
    shipped.pop("generated_for")
    generated.pop("generated_for")
    assert shipped == generated, (
        "chimera/_benchmark_snapshot.json is out of sync with its generator — regenerate it with "
        "`python -m chimera.eval.benchmark_snapshot` instead of editing the JSON by hand"
    )


def test_benchmark_report_available_on_this_repo() -> None:
    report = benchmark_report()

    # The snapshot ships inside the package, so the report is available here.
    assert report["available"] is True
    lift = report["internal_lift"]
    assert lift is not None
    # The significance flag must never drift from the CI it is published beside — the one honesty
    # property that is checkable WITHOUT the gitignored results file, so CI enforces it on every run.
    # (Not a hardcoded literal: it was False at n=15 and is True at n=100; what must hold is agreement.)
    lo, hi = lift["ci"]
    assert isinstance(lift["significant"], bool)
    assert lift["significant"] is (lo > 0.0 or hi < 0.0), (
        f"published significance {lift['significant']} contradicts its own CI [{lo}, {hi}]"
    )
    assert lift["n"] > 0
    assert report["external"]
    assert isinstance(report["generated_for"], str)


def test_benchmark_report_reads_a_written_snapshot(tmp_path: Path) -> None:
    # Round-trips the SHIPPED snapshot (committed) rather than regenerating from the gitignored
    # results file, so this runs on a fresh clone too.
    snap_file = tmp_path / "_benchmark_snapshot.json"
    snap_file.write_text(snapshot_path().read_text(encoding="utf-8"), encoding="utf-8")

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
