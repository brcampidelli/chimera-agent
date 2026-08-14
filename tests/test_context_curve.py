"""The curve, and the thing it must refuse to draw.

This analysis exists to decide whether to build context compaction. The failure it is built against
is not arithmetic — it is drawing a confident line through six data points and calling it a reason to
spend two weeks. So most of what is tested here is the refusal: below the pre-registered floors it
reports "not enough data" and makes no claim in either direction.

That refusal is also its first real output. At the time of writing, the join returns zero rows on
this machine and on the production VPS.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.eval.context_curve import MIN_PER_BUCKET, context_curve, wilson


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _traces(path: Path, entries: list[tuple[str, int]]) -> None:
    _write(path, [{"run_id": rid, "context_peak_tokens": peak} for rid, peak in entries])


def _runs(path: Path, entries: list[tuple[str, bool]]) -> None:
    _write(path, [{"attempts": [{"run_id": rid, "success": ok}]} for rid, ok in entries])


# --- the refusal -------------------------------------------------------------------------------


def test_no_data_is_not_a_null_result(tmp_path: Path) -> None:
    """The distinction the whole file turns on. "The effect was looked for and not found" and
    "nobody has looked yet" license completely different decisions, and only one of them is true
    today."""
    result = context_curve(tmp_path / "traces.jsonl", tmp_path / "runs.jsonl")

    assert result.enough is False
    assert "not enough data" in result.verdict()
    assert result.joined == 0


def test_a_thin_bucket_reports_no_rate_at_all(tmp_path: Path) -> None:
    # Not a rate with a wide interval — no rate. A point estimate invites being quoted without it.
    traces, runs = tmp_path / "t.jsonl", tmp_path / "r.jsonl"
    few = [(f"r{i}", 1_000) for i in range(MIN_PER_BUCKET - 1)]
    _traces(traces, few)
    _runs(runs, [(rid, True) for rid, _ in few])

    result = context_curve(traces, runs)

    assert result.buckets[0].runs == MIN_PER_BUCKET - 1
    assert result.buckets[0].rate is None
    assert result.enough is False


def test_two_populated_buckets_are_still_not_enough(tmp_path: Path) -> None:
    """With two, "it declines with context" cannot be told apart from "one bucket is unlucky"."""
    traces, runs = tmp_path / "t.jsonl", tmp_path / "r.jsonl"
    entries = [(f"a{i}", 1_000) for i in range(30)] + [(f"b{i}", 20_000) for i in range(30)]
    _traces(traces, entries)
    _runs(runs, [(rid, i % 2 == 0) for i, (rid, _) in enumerate(entries)])

    result = context_curve(traces, runs)

    assert result.joined == 60
    assert sum(1 for b in result.buckets if b.rate is not None) == 2
    assert result.enough is False


# --- what it says when there IS data -----------------------------------------------------------


def _three_buckets(tmp_path: Path, rates: tuple[float, float, float], n: int = 40):
    traces, runs = tmp_path / "t.jsonl", tmp_path / "r.jsonl"
    peaks = (1_000, 20_000, 60_000)
    entries, outcomes = [], []
    for band, (peak, rate) in enumerate(zip(peaks, rates, strict=True)):
        for i in range(n):
            rid = f"{band}-{i}"
            entries.append((rid, peak))
            outcomes.append((rid, i < round(rate * n)))
    _traces(traces, entries)
    _runs(runs, outcomes)
    return context_curve(traces, runs)


def test_a_clear_decline_is_reported_as_one(tmp_path: Path) -> None:
    result = _three_buckets(tmp_path, (0.95, 0.60, 0.15))

    assert result.enough is True
    assert "falls with context" in result.verdict()


def test_overlapping_intervals_are_reported_as_no_separation(tmp_path: Path) -> None:
    # The outcome that would SAVE work: it says, in as many words, not to build the thing.
    result = _three_buckets(tmp_path, (0.60, 0.58, 0.62))

    assert result.enough is True
    assert "no separation" in result.verdict()
    assert "not the lever" in result.verdict()


def test_the_opposite_effect_is_not_quietly_reported_as_a_decline(tmp_path: Path) -> None:
    """A curve going the wrong way usually means the apparatus, not the phenomenon — and a report
    that phrased it as "no decline found" would hide the surprise."""
    result = _three_buckets(tmp_path, (0.15, 0.55, 0.95))

    assert "RISES" in result.verdict()


# --- the join ----------------------------------------------------------------------------------


def test_unjoinable_rows_are_counted_not_dropped(tmp_path: Path) -> None:
    """A silently dropped half of the data is how a real effect gets measured away — and every
    receipt written before the run id exists is exactly that half."""
    traces, runs = tmp_path / "t.jsonl", tmp_path / "r.jsonl"
    _traces(traces, [("known", 5_000), ("orphan", 9_000)])
    _runs(runs, [("known", True), ("", False), ("missing", True)])

    result = context_curve(traces, runs)

    assert result.joined == 1
    assert result.unjoinable_attempts == 2  # the empty id and the one with no trace
    assert result.unjoinable_traces == 1  # "orphan" was never claimed by an attempt


def test_it_scores_the_attempt_not_the_run(tmp_path: Path) -> None:
    """A run's success flag is its LAST attempt's. Using it would credit a late success to the
    context an early, failed attempt was carrying — smearing the very relationship measured."""
    traces, runs = tmp_path / "t.jsonl", tmp_path / "r.jsonl"
    _traces(traces, [("first", 1_000), ("second", 1_000)])
    runs.write_text(
        json.dumps(
            {
                "success": True,
                "attempts": [
                    {"run_id": "first", "success": False},
                    {"run_id": "second", "success": True},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = context_curve(traces, runs)

    assert result.buckets[0].runs == 2
    assert result.buckets[0].successes == 1, "the failed attempt was credited with the run's success"


def test_a_torn_last_line_does_not_lose_the_file(tmp_path: Path) -> None:
    # Append-only logs get truncated by crashes, and this one is written by a daemon.
    traces, runs = tmp_path / "t.jsonl", tmp_path / "r.jsonl"
    _traces(traces, [("a", 1_000)])
    traces.write_text(traces.read_text(encoding="utf-8") + '{"run_id": "b", "cont', encoding="utf-8")
    _runs(runs, [("a", True)])

    assert context_curve(traces, runs).joined == 1


# --- the interval ------------------------------------------------------------------------------


def test_wilson_stays_inside_the_unit_interval_at_small_n() -> None:
    """The normal approximation hands back a negative lower bound at 1/5 and makes a thin bucket
    look decisive. That is precisely the shape this analysis must not produce."""
    low, high = wilson(1, 5)

    assert 0.0 <= low <= high <= 1.0
    assert high - low > 0.4, "an interval this narrow at n=5 would be a fabrication"
