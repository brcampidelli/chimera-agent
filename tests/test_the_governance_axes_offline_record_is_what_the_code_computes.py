"""The committed offline record of `bench/governance_axes` is what the code computes today (S30-36).

Axes 1-3 call no model, so their RESULTS can be pinned exactly: a change to the kernel rules, the
taint ledger, the open-privilege instrument or the preemption corpus that moves a published number
turns this red, and the RESULTS have to be re-read rather than silently going stale (§2z).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "bench" / "governance_axes"
RECORD = HERE / "results" / "offline.json"
OATS = ROOT / "bench" / "denylist_bypass" / "results" / "oats.jsonl"


def _load_run() -> Any:
    # Several bench scripts are called run.py; load this one under a name of its own.
    sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location("governance_axes_run", HERE / "run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not RECORD.exists() or not OATS.exists(),
                    reason="a copy without bench/*/results (the WSL gate excludes them)")
def test_the_committed_offline_record_equals_a_fresh_run(capsys: pytest.CaptureFixture[str]) -> None:
    run = _load_run()
    fresh = {"open_privilege": run.axis_open_privilege(), "joint": run.axis_joint(),
             "preemption": run.axis_preemption()}
    capsys.readouterr()
    assert json.loads(json.dumps(fresh, default=list)) == json.loads(RECORD.read_text(encoding="utf-8"))


@pytest.mark.skipif(not RECORD.exists(), reason="a copy without bench/*/results (the WSL gate excludes them)")
def test_the_uncomfortable_numbers_the_results_name_are_the_recorded_ones() -> None:
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    clean = record["open_privilege"]["workspace/shipped"]
    assert (clean["open_cells"], clean["cells"]) == (15, 21)  # A2: 71.4% of the probes stay open
    tainted = record["open_privilege"]["fetch/taint"]
    assert (tainted["own_task_runs"], tainted["own_task_total"]) == (0, 5)  # the taint layer refuses the work too
    told_apart = record["preemption"]["workspace/shipped"]["told_apart"]
    assert told_apart == 1  # ownership is read on 1 of 16 preemptions


def test_the_clean_run_headline_is_a_count_of_distinct_probes_not_a_pooled_interval() -> None:
    # The 21 clean-run cells are 3 deterministic copies of the same 7 probe outcomes. A Wilson over
    # 21 counts each probe three times; the registered t over tasks has no spread to read. The
    # headline is the count over the 7 probes, and any width quoted beside it is over those 7.
    from chimera.eval.open_privilege import run_open_privilege
    from chimera.eval.proportions import mean_t_interval, wilson

    report = run_open_privilege()
    clean = report.per_task("shipped", source="workspace")
    assert len(clean) == 3 and set(clean.values()) == {5 / 7}
    _, low, high = mean_t_interval(list(clean.values()))
    assert (low, high) == (float("-inf"), float("inf"))

    results = (HERE / "RESULTS.md").read_text(encoding="utf-8")
    verdict_table = results.split("## Predictions")[0]
    row = next(line for line in verdict_table.splitlines() if line.startswith("| 1. open privilege, clean run"))
    assert "5 of the 7 probes" in row and "no interval" in row
    assert "Wilson" not in row  # the pooled [50.0%, 86.2%] is no longer the headline's width
    low7, high7 = wilson(5, 7)
    assert f"[{low7:.1%}, {high7:.1%}]" in results
    assert "How the headline is read (corrected 2026-10-06)" in results  # the change is recorded, not silent
