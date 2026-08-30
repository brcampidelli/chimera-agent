"""The Cost screen must count what the runs spent, because runs are the expensive part.

``record_spend`` writes ``usage.jsonl`` and has exactly two callers — a chat turn and an
orchestration run. The autonomous run path is not one of them: it writes its receipts to
``runs.jsonl``, which the screen never read.

Measured in the desktop app: a day with **$0.0270** of runs showed a total that stopped at the
previous day, and the screen's headline figure was the chat spend alone. That is worse than an
absent dashboard — an absent one is not consulted, and this one answered "how much have I spent"
with a number that was confidently too low, in the one direction nobody checks.

Everything here is free: no model call, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.api.usage import (
    RUN_SESSION_PREFIX,
    UsageRecord,
    append_usage,
    load_usage,
    summarize_usage,
    usage_from_runs,
)


def _run_row(**kw: Any) -> dict[str, Any]:
    attempt = {
        "index": 1,
        "run_id": kw.pop("run_id", "r1"),
        "model": kw.pop("model", "openrouter/deepseek/deepseek-chat-v3.1"),
        "prompt_tokens": kw.pop("prompt_tokens", 8870),
        "completion_tokens": kw.pop("completion_tokens", 193),
        "usd": kw.pop("usd", 0.005197),
        "overhead_usd": kw.pop("overhead_usd", None),
        "tool_names": kw.pop("tool_names", ["read_file"]),
    }
    return {
        "ts": kw.pop("ts", "2026-08-30T04:21:23.067048+00:00"),
        "task": "do the thing",
        "success": False,
        "workspace": "C:/w",
        "attempts": [attempt, *kw.pop("extra_attempts", [])],
    }


def _write_runs(home: Path, *rows: dict[str, Any]) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "runs.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# --- the receipts become usage records ---------------------------------------------------------


def test_a_run_attempt_is_counted(tmp_path: Path) -> None:
    records = usage_from_runs(_write_runs(tmp_path, _run_row()))

    assert len(records) == 1
    assert records[0].usd == 0.005197
    assert records[0].prompt_tokens == 8870
    assert records[0].model == "openrouter/deepseek/deepseek-chat-v3.1"


def test_every_attempt_counts_not_just_the_run(tmp_path: Path) -> None:
    """Three attempts are three model calls and three bills. Counting the run once would report a
    three-attempt run at the price of one — the exact shape of the under-count being fixed."""
    row = _run_row(
        extra_attempts=[
            {"index": 2, "run_id": "r1", "model": "m", "prompt_tokens": 10,
             "completion_tokens": 1, "usd": 0.002},
            {"index": 3, "run_id": "r1", "model": "m", "prompt_tokens": 10,
             "completion_tokens": 1, "usd": 0.003},
        ]
    )

    total = summarize_usage(usage_from_runs(_write_runs(tmp_path, row)))

    assert total["totals"]["turns"] == 3
    assert round(total["totals"]["usd"], 6) == 0.010197


def test_an_unpriced_attempt_is_unknown_never_zero(tmp_path: Path) -> None:
    """The rule this module already holds for chat turns: an unknown price is counted apart, not
    laundered into a $0 that makes the total look complete."""
    summary = summarize_usage(usage_from_runs(_write_runs(tmp_path, _run_row(usd=None))))

    assert summary["totals"]["unpriced_turns"] == 1
    assert summary["totals"]["usd"] == 0.0


def test_overhead_is_a_share_of_the_price_not_an_addition(tmp_path: Path) -> None:
    """``Attempt.overhead_usd`` documents itself as "the share of ``usd`` that was NOT the worker".
    Adding it on top would over-report by the planner and reviewer legs of every attempt."""
    records = usage_from_runs(_write_runs(tmp_path, _run_row(usd=0.0058, overhead_usd=0.000608)))

    assert records[0].usd == 0.0058


# --- the two logs do not collide ---------------------------------------------------------------


def test_a_run_and_a_chat_turn_stay_separate_sessions(tmp_path: Path) -> None:
    """The merge counts each side once because the id namespaces cannot overlap: chat writes a bare
    session id, orchestration writes ``orchestration:``, runs write ``run:``."""
    append_usage(tmp_path / "usage.jsonl", UsageRecord(ts="2026-08-30T00:00:00+00:00",
                                                       session_id="r1", model="m", usd=1.0))

    merged = load_usage(tmp_path / "usage.jsonl") + usage_from_runs(_write_runs(tmp_path, _run_row()))
    summary = summarize_usage(merged)

    sessions = {s["session_id"] for s in summary["by_session"]}
    assert sessions == {"r1", RUN_SESSION_PREFIX + "r1"}
    assert summary["totals"]["turns"] == 2


# --- it survives a bad file --------------------------------------------------------------------


def test_a_missing_log_is_empty_not_an_error(tmp_path: Path) -> None:
    assert usage_from_runs(tmp_path / "nothing.jsonl") == []


def test_a_malformed_line_is_skipped_and_the_rest_still_counts(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_text("{not json\n" + json.dumps(_run_row()) + "\n", encoding="utf-8")

    assert len(usage_from_runs(path)) == 1


# --- the wiring, not just the function ---------------------------------------------------------


def test_the_route_actually_merges_them(tmp_path: Path) -> None:
    """A summariser that works while the endpoint never calls it is a guard outside the flow — the
    failure mode this repo has hit before, so the assertion goes through the HTTP surface."""
    from tests.test_api import _client  # noqa: PLC0415

    client = _client(tmp_path)
    home = tmp_path / "home"
    _write_runs(home, _run_row(usd=0.0270))

    body = client.get("/api/usage").json()

    assert body["totals"]["turns"] == 1
    assert round(body["totals"]["usd"], 6) == 0.027


# --- the panel that shows where the money went -------------------------------------------------


def test_the_session_panel_keeps_the_expensive_ones_not_the_first_ones(tmp_path: Path) -> None:
    """The panel holds 20 rows and nearly every session is one turn, so the sort key decides which
    20 by breaking a tie. Ordered by input, the tie went to whichever log was concatenated first —
    and runs are concatenated last, so the merge would have pushed every run off the one panel
    whose subject is where the money went.
    """
    cheap = [
        UsageRecord(ts="2026-08-30T00:00:00+00:00", session_id=f"chat{i}", model="m", usd=0.0001)
        for i in range(25)
    ]
    expensive = usage_from_runs(_write_runs(tmp_path, _run_row(run_id="pricey", usd=9.99)))

    panel = summarize_usage(cheap + expensive)["by_session"]

    assert len(panel) == 20
    assert panel[0]["session_id"] == RUN_SESSION_PREFIX + "pricey"


def test_more_turns_still_outrank_a_bigger_bill(tmp_path: Path) -> None:
    """Spend is the TIE-break, not the sort. A three-attempt session is three turns and stays above
    a one-turn session however expensive that one turn was — the panel is still by activity."""
    one_big = [UsageRecord(ts="2026-08-30T00:00:00+00:00", session_id="whale", model="m", usd=50.0)]
    three_small = [
        UsageRecord(ts="2026-08-30T00:00:00+00:00", session_id="busy", model="m", usd=0.01)
        for _ in range(3)
    ]

    panel = summarize_usage(one_big + three_small)["by_session"]

    assert panel[0]["session_id"] == "busy"
