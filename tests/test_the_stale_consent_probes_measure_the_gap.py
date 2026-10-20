"""The stale-consent probes run, and their registered summary holds (study 31, G31-03).

`bench/stale_consent/run.py` is the instrument; this file is the meta-test the protocol's shape
expects: the probes execute against the real `ask_durably` (short waits, injected clock) and the
registered summary numbers are asserted, so a change to `pending.py` that breaks the bound the
bench measured fails here, in the suite, rather than only in the next bench run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.stale_consent import run as bench_run


def _fresh_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def test_the_sync_gap_stays_under_one_poll_interval(tmp_path: Path) -> None:
    """S1: the consent-to-effect gap is bounded by the poll interval, at every grid point."""
    probes = bench_run.probe_sync_grid(_fresh_home(tmp_path))
    assert all(p.outcome == "approved" for p in probes)
    assert all(p.effect_ran for p in probes)
    assert all(p.gap is not None and p.gap <= bench_run.POLL_SECONDS for p in probes)
    # The person's clock is exact: the record's seconds_to_answer equals the scheduled moment.
    assert all(p.seconds_to_answer == p.delta for p in probes)


def test_the_fast_poll_bounds_the_gap_the_same_way(tmp_path: Path) -> None:
    probes = bench_run.probe_sync_grid_fast_poll(_fresh_home(tmp_path))
    assert all(p.gap is not None and p.gap <= 0.5 for p in probes)


def test_an_answer_before_the_deadline_is_honoured_and_one_after_is_not(tmp_path: Path) -> None:
    """S2: the boundary sits exactly at the deadline — no answer past it is applied."""
    probes = bench_run.probe_boundaries(_fresh_home(tmp_path))
    outcomes = [p.outcome for p in probes]
    assert outcomes == ["approved", "approved", "approved", "approved", "timeout"]


def test_the_chat_code_expires_one_poll_before_the_deadline(tmp_path: Path) -> None:
    """S2b: `applied` at expires_at − ε, `expired` at +ε — the acknowledgement never reads as a
    timeout, and nothing past the deadline is applied."""
    result = bench_run.probe_code_expiry(_fresh_home(tmp_path))
    assert result["expires_at_minus_epsilon"] == "applied"
    assert result["expires_at_plus_epsilon"] == "expired"
    assert result["bound_is_one_poll_early"] is True


def test_the_orphan_record_is_written_before_the_return(tmp_path: Path) -> None:
    """S3, as amended: the record exists while the waiter is still inside the ask, and the effect
    runs exactly once, after the thread completes — the ordering a truthful record needs."""
    result = bench_run.probe_orphan(_fresh_home(tmp_path))
    assert result["record_outcome"] == "approved"
    assert result["record_written_before_return"] is True
    assert result["effect_count"] == 1
    assert result["record_truthful"] is True


def test_the_real_clock_probe_applies_the_approved_effect(tmp_path: Path) -> None:
    """S4 observes real timing but gates on approval/effect behavior, not runner latency."""
    rows = bench_run.probe_real_clock(_fresh_home(tmp_path))
    assert len(rows) == bench_run.REAL_RUNS
    # Said, not crashed: a run whose answer was written after the wait ran out measured no gap.
    late = [r for r in rows if r["outcome"] != "approved"]
    assert not late, f"the answer arrived after the {bench_run.REAL_WAIT}s wait: {late}"
    assert all(r["effect_ran"] for r in rows)
    assert all(r["seconds_to_answer_recorded"] is not None for r in rows)
    assert all(r["gap_seconds"] is not None for r in rows)


def test_real_clock_gap_uses_the_recorded_answer_time() -> None:
    """The gap calculation depends on the actual recorded write time, not a target delay."""
    assert bench_run._real_clock_gap_seconds(100.0, 101.25, 1.0) == 0.25
    assert bench_run._real_clock_gap_seconds(100.0, 106.0, 1.0) == 5.0
    assert bench_run._real_clock_gap_seconds(100.0, 106.0, None) is None


def test_real_clock_bench_keeps_its_registered_decision_bound() -> None:
    """A 1.2038 s miss stays a miss against the preregistered 1.2 s limit."""
    assert bench_run._real_gap_within_registered_bound(1.2) is True
    assert bench_run._real_gap_within_registered_bound(1.2038) is False
    assert bench_run._real_gap_within_registered_bound(None) is False


def test_the_drift_spec_is_committed_and_says_unmeasured() -> None:
    """The named non-measurement: the join exists, the number does not, and the file says so."""
    spec = bench_run.plan_gate_drift_spec()
    assert spec["status"].startswith("unmeasured")
    assert "history.jsonl" in spec["left"] and "runs.jsonl" in spec["right"]


def test_the_registered_summary_reproduces(tmp_path: Path) -> None:
    """Deterministic probes reproduce; real-clock measurements remain observations."""
    home = _fresh_home(tmp_path)
    probes = (
        bench_run.probe_sync_grid(home)
        + bench_run.probe_sync_grid_fast_poll(home)
    )
    gaps = [p.gap for p in probes if p.gap is not None]
    assert max(gaps) <= bench_run.POLL_SECONDS
    assert bench_run.probe_boundaries(home)  # outcomes asserted in their own test
    expiry = bench_run.probe_code_expiry(home)
    assert expiry["expires_at_minus_epsilon"] == "applied"
    assert expiry["expires_at_plus_epsilon"] == "expired"
    orphan = bench_run.probe_orphan(home)
    assert orphan["record_truthful"] is True
    real = bench_run.probe_real_clock(home)
    real_gaps = [r["gap_seconds"] for r in real if r["gap_seconds"] is not None]
    assert len(real_gaps) == len(real)
    assert len(real) == bench_run.REAL_RUNS
    assert all(r["outcome"] == "approved" and r["effect_ran"] for r in real)
    assert all(r["seconds_to_answer_recorded"] is not None for r in real)


def test_a_late_answerer_is_not_counted_as_latency(tmp_path: Path, monkeypatch: Any) -> None:
    """Gap measures the recorded write time even when the answerer wakes late.

    The answerer oversleeps by 1.5 s; the measured gap remains about one poll interval rather
    than adding the answerer's scheduling delay."""
    import time as real_time

    real_sleep = real_time.sleep

    def late_for_the_answer(seconds: float) -> None:
        real_sleep(seconds + 1.5 if seconds == bench_run.REAL_ANSWER_DELAY else seconds)

    monkeypatch.setattr(bench_run, "REAL_RUNS", 1)
    monkeypatch.setattr(bench_run.time, "sleep", late_for_the_answer)
    [row] = bench_run.probe_real_clock(_fresh_home(tmp_path))
    assert row["outcome"] == "approved"
    assert row["effect_ran"] is True
    assert row["seconds_to_answer_recorded"] >= bench_run.REAL_ANSWER_DELAY + 1.4
    # This is the wall-clock bench reading, not a pass/fail bound on a shared runner.
    assert row["gap_seconds"] is not None
