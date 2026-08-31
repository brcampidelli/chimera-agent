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
    _already_counted,
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
    """Two different sessions stay two rows in the panel.

    This used to be cited as the reason the merge could not double-count, and that argument was
    wrong: id namespaces not colliding says nothing about the same WORK being written to both
    files under different names, which is exactly what a scheduled run does. The property this
    checks is the grouping — see the dedupe tests below for the one that actually protects money.
    """
    append_usage(tmp_path / "usage.jsonl", UsageRecord(ts="2026-08-30T00:00:00+00:00",
                                                       session_id="r1", model="m", usd=1.0))

    merged = load_usage(tmp_path / "usage.jsonl") + usage_from_runs(_write_runs(tmp_path, _run_row()))
    summary = summarize_usage(merged)

    sessions = {s["session_id"] for s in summary["by_session"]}
    assert sessions == {"r1", RUN_SESSION_PREFIX + "r1"}
    assert summary["totals"]["turns"] == 2


# --- the same work must not be billed twice ------------------------------------------------------


def test_a_scheduled_run_written_to_both_logs_is_counted_once(tmp_path: Path) -> None:
    """The measured defect: a scheduled run writes a usage row keyed ``cron:<job>:<run_id>`` AND a
    receipt carrying the same ``run_id``. Four of them on a real install — $0.0449 billed twice.
    """
    append_usage(tmp_path / "usage.jsonl", UsageRecord(
        ts="2026-08-30T14:39:43+00:00", session_id="cron:c8b9a2e3:32002e09", model="m", usd=0.01085,
        prompt_tokens=19000, completion_tokens=412,
    ))
    turnos = load_usage(tmp_path / "usage.jsonl")
    runs = _write_runs(tmp_path, _run_row(run_id="32002e09", usd=0.01085))

    summary = summarize_usage(turnos + usage_from_runs(runs, already=_already_counted(turnos)))

    assert summary["totals"]["turns"] == 1, "the same run was counted from both logs"
    assert round(summary["totals"]["usd"], 6) == 0.01085


def test_without_the_join_it_really_would_be_billed_twice(tmp_path: Path) -> None:
    """The control. Without it, the assertion above could pass because the fixture never overlapped
    — and this test file's first version passed for exactly that kind of reason."""
    append_usage(tmp_path / "usage.jsonl", UsageRecord(
        ts="2026-08-30T14:39:43+00:00", session_id="cron:c8b9a2e3:32002e09", model="m", usd=0.01085,
    ))
    turnos = load_usage(tmp_path / "usage.jsonl")
    runs = _write_runs(tmp_path, _run_row(run_id="32002e09", usd=0.01085))

    sem_juncao = summarize_usage(turnos + usage_from_runs(runs))

    assert sem_juncao["totals"]["turns"] == 2
    assert round(sem_juncao["totals"]["usd"], 6) == 0.0217


def test_a_scheduled_run_with_SEVERAL_attempts_is_counted_once(tmp_path: Path) -> None:
    """The half the first join missed, in the exact shape it was measured.

    A scheduled dispatch writes ONE usage row holding the total across every attempt, keyed by the
    LAST attempt's id. Skipping only the matching attempt left the earlier ones to be added on top
    of a total that already contained them: two attempts of $0.004247 and $0.006598 against a
    $0.010845 row came out as $0.015092.
    """
    append_usage(tmp_path / "usage.jsonl", UsageRecord(
        ts="2026-08-31T02:02:30+00:00", session_id="cron:8e9681d2:e0e62070", model="m",
        usd=0.010845,
    ))
    turnos = load_usage(tmp_path / "usage.jsonl")
    runs = _write_runs(tmp_path, _run_row(
        run_id="0c2854db", usd=0.004247,
        extra_attempts=[{"index": 2, "run_id": "e0e62070", "model": "m",
                         "prompt_tokens": 10, "completion_tokens": 1, "usd": 0.006598}],
    ))

    summary = summarize_usage(turnos + usage_from_runs(runs, already=_already_counted(turnos)))

    assert summary["totals"]["turns"] == 1, "an earlier attempt was added on top of its own total"
    assert round(summary["totals"]["usd"], 6) == 0.010845


def test_a_run_none_of_whose_attempts_are_counted_survives_whole(tmp_path: Path) -> None:
    """The control for the rule above: skipping the WHOLE run is right only when the run is
    already counted. A run the usage log has never seen must keep every attempt."""
    turnos = load_usage(tmp_path / "usage.jsonl")
    runs = _write_runs(tmp_path, _run_row(
        run_id="aaa", usd=0.01,
        extra_attempts=[{"index": 2, "run_id": "bbb", "model": "m",
                         "prompt_tokens": 10, "completion_tokens": 1, "usd": 0.02}],
    ))

    summary = summarize_usage(turnos + usage_from_runs(runs, already=_already_counted(turnos)))

    assert summary["totals"]["turns"] == 2
    assert round(summary["totals"]["usd"], 6) == 0.03


def test_a_bare_chat_session_is_never_read_as_a_run_id(tmp_path: Path) -> None:
    """The dangerous direction. A chat turn writes a bare 32-hex session id and no receipt; reading
    one as a run id would silently DROP a real charge, and under-counting money must never happen
    by accident."""
    append_usage(tmp_path / "usage.jsonl", UsageRecord(
        ts="2026-08-30T00:00:00+00:00", session_id="32002e09", model="m", usd=1.0,
    ))
    turnos = load_usage(tmp_path / "usage.jsonl")
    runs = _write_runs(tmp_path, _run_row(run_id="32002e09", usd=0.5))

    summary = summarize_usage(turnos + usage_from_runs(runs, already=_already_counted(turnos)))

    assert summary["totals"]["turns"] == 2, "a real charge was dropped"
    assert round(summary["totals"]["usd"], 6) == 1.5


def test_an_ordinary_run_is_still_counted(tmp_path: Path) -> None:
    """The other half: a run that is NOT in the usage log has to survive the join."""
    turnos = load_usage(tmp_path / "usage.jsonl")
    runs = _write_runs(tmp_path, _run_row(run_id="nunca-visto", usd=0.02))

    summary = summarize_usage(turnos + usage_from_runs(runs, already=_already_counted(turnos)))

    assert round(summary["totals"]["usd"], 6) == 0.02


def test_the_route_joins_them_too(tmp_path: Path) -> None:
    """Through HTTP, because a join the endpoint does not perform is a join nobody performs."""
    from tests.test_api import _client  # noqa: PLC0415

    client = _client(tmp_path)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    append_usage(home / "usage.jsonl", UsageRecord(
        ts="2026-08-30T14:39:43+00:00", session_id="cron:j1:abc123", model="m", usd=0.01,
    ))
    _write_runs(home, _run_row(run_id="abc123", usd=0.01))

    body = client.get("/api/usage").json()

    assert body["totals"]["turns"] == 1
    assert round(body["totals"]["usd"], 6) == 0.01


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
