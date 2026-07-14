"""Tests for the usage log (append-only JSONL) and its honest aggregation.

The load-bearing property: a turn whose price is unknown (``usd=None``) is COUNTED as unpriced and
never SUMMED as 0 — the total spend only adds prices it actually knows.
"""

from __future__ import annotations

from typing import Any

from chimera.api.usage import UsageRecord, append_usage, load_usage, summarize_usage


def test_append_and_load_roundtrip(tmp_path: Any) -> None:
    path = tmp_path / "usage.jsonl"
    records = [
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", model="gpt", prompt_tokens=10),
        UsageRecord(ts="2026-07-13T11:00:00+00:00", session_id="s2", model="claude", usd=0.002),
    ]
    for r in records:
        append_usage(path, r)
    loaded = load_usage(path)
    assert [r.model for r in loaded] == ["gpt", "claude"]
    assert loaded[0].prompt_tokens == 10 and loaded[1].usd == 0.002


def test_load_missing_file_is_empty(tmp_path: Any) -> None:
    assert load_usage(tmp_path / "nope.jsonl") == []


def test_load_skips_malformed_lines(tmp_path: Any) -> None:
    path = tmp_path / "usage.jsonl"
    append_usage(path, UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n\n")  # garbage + a blank line
    assert len(load_usage(path)) == 1


def test_summarize_usd_sums_only_priced_and_counts_unpriced() -> None:
    records = [
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", usd=0.01),
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", usd=None),  # unknown price
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", usd=0.02),
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", usd=None),  # unknown price
    ]
    totals = summarize_usage(records)["totals"]
    assert totals["turns"] == 4
    assert totals["usd"] == 0.03  # 0.01 + 0.02 only — the two None turns are NOT added as 0
    assert totals["unpriced_turns"] == 2


def test_summarize_empty_is_honest() -> None:
    s = summarize_usage([])
    assert s["totals"]["turns"] == 0
    assert s["totals"]["usd"] == 0.0 and s["totals"]["unpriced_turns"] == 0
    assert s["by_day"] == [] and s["by_model"] == [] and s["by_session"] == []
    assert s["cache_hit_pct"] is None
    assert s["route_mix"] == {"single": 0, "fusion": 0, "cascade": 0}


def test_summarize_by_day_grouped_and_sorted() -> None:
    records = [
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", prompt_tokens=5, usd=0.01),
        UsageRecord(ts="2026-07-11T09:00:00+00:00", session_id="s1", prompt_tokens=3, usd=None),
        UsageRecord(ts="2026-07-13T22:00:00+00:00", session_id="s2", prompt_tokens=7, usd=0.02),
    ]
    by_day = summarize_usage(records)["by_day"]
    assert [d["day"] for d in by_day] == ["2026-07-11", "2026-07-13"]  # ascending
    jul13 = by_day[1]
    assert jul13["turns"] == 2 and jul13["prompt_tokens"] == 12
    assert jul13["usd"] == 0.03  # only the two priced turns on that day
    # Per-group honesty: a fully-unpriced day carries unpriced==turns (the UI shows "—", not a fake $0);
    # a fully-priced day carries unpriced==0.
    assert by_day[0]["usd"] == 0.0 and by_day[0]["unpriced"] == 1  # sole Jul-11 turn was unpriced
    assert jul13["unpriced"] == 0  # both Jul-13 turns were priced


def test_summarize_by_model_sorted_by_turns_desc() -> None:
    records = [
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", model="a", usd=0.01),
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", model="b", usd=0.01),
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", model="b", usd=0.01),
    ]
    by_model = summarize_usage(records)["by_model"]
    assert [m["model"] for m in by_model] == ["b", "a"]  # b has 2 turns, a has 1
    assert by_model[0]["turns"] == 2


def test_summarize_cache_hit_and_route_mix() -> None:
    records = [
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", prompt_tokens=30,
                    cache_read_tokens=10, route_kind=None),
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", prompt_tokens=10,
                    cache_read_tokens=0, route_kind="fusion"),
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", route_kind="cascade"),
        UsageRecord(ts="2026-07-13T10:00:00+00:00", session_id="s1", route_kind="mystery"),
    ]
    s = summarize_usage(records)
    # cache_read / (prompt + cache_read) = 10 / (40 + 10) = 0.2
    assert s["cache_hit_pct"] == 0.2
    # None -> single; "mystery" (unknown kind) also buckets to single
    assert s["route_mix"] == {"single": 2, "fusion": 1, "cascade": 1}
