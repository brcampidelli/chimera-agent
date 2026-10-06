"""The stale-consent probes run, and their registered summary holds (study 31, G31-03).

`bench/stale_consent/run.py` is the instrument; this file is the meta-test the protocol's shape
expects: the probes execute against the real `ask_durably` (short waits, injected clock) and the
registered summary numbers are asserted, so a change to `pending.py` that breaks the bound the
bench measured fails here, in the suite, rather than only in the next bench run.
"""

from __future__ import annotations

from pathlib import Path

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


def test_the_real_clock_gap_is_milliseconds(tmp_path: Path) -> None:
    """S4: on the real clock the gap is scheduling noise, not seconds."""
    rows = bench_run.probe_real_clock(_fresh_home(tmp_path))
    assert len(rows) == bench_run.REAL_RUNS
    assert all(r["outcome"] == "approved" for r in rows)
    assert all(r["gap_seconds"] <= bench_run.REAL_POLL + 1.0 for r in rows)


def test_the_drift_spec_is_committed_and_says_unmeasured() -> None:
    """The named non-measurement: the join exists, the number does not, and the file says so."""
    spec = bench_run.plan_gate_drift_spec()
    assert spec["status"].startswith("unmeasured")
    assert "history.jsonl" in spec["left"] and "runs.jsonl" in spec["right"]


def test_the_registered_summary_reproduces(tmp_path: Path) -> None:
    """The whole run, end to end, reproduces the registered verdict on a fresh home."""
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
    assert max(r["gap_seconds"] for r in real) <= bench_run.REAL_POLL + 1.0
