"""A retry is not an independent draw — the ruler that can say so, and can also say "no effect".

Synthetic receipts only, shaped like the desktop's `runs.jsonl`. Fixture (i) is built so retries ARE
independent — every row recovers at exactly the previous row's rate — and the probe must not flag
it: a ruler that cannot say "no effect" is not a ruler (§2s). Fixture (ii) recovers nothing where
independence predicts five of ten, and must be flagged. The binomial tail is pinned to values that
can be checked by hand, the money shares must sum to one, and a receipt missing what the probe needs
must come back as ``None`` or as *unreadable* — never as a plausible number.

Every guard below was broken on disk and its test confirmed red.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from chimera.eval.retries import (
    ReceiptFile,
    RunOutcome,
    binomial_tail,
    conditional_rows,
    flags_contamination,
    format_report,
    max_rate_consistent,
    money_by_index,
    parse_receipt,
    pass_at_k,
    read_receipts,
    zero_successes_needed,
)

# --- synthetic receipts -------------------------------------------------------------------------


def _receipt(
    outcomes: Sequence[bool],
    usd: Sequence[float | None] | None = None,
    *,
    stopped_reason: str = "",
    task: str = "the task",
    max_attempts: int | None = None,
) -> dict[str, object]:
    """One receipt with the fields the probe reads, and nothing the probe must never print."""
    attempts: list[dict[str, object]] = []
    for position, ok in enumerate(outcomes):
        attempts.append(
            {
                "index": position + 1,
                "success": ok,
                "usd": 0.01 if usd is None else usd[position],
                "tool_names": ["edit_file"],
                "prompt_tokens": 100,
                "completion_tokens": 10,
            }
        )
    record: dict[str, object] = {
        "ts": "2026-09-08T00:00:00",
        "task": task,
        "success": any(outcomes),
        "attempts": attempts,
        "stopped_reason": stopped_reason,
    }
    if max_attempts is not None:
        record["max_attempts"] = max_attempts
    return record


def _runs(records: Sequence[dict[str, object]]) -> list[RunOutcome]:
    parsed = [parse_receipt(r) for r in records]
    assert all(p is not None for p in parsed)
    return [p for p in parsed if p is not None]


def _file(records: Sequence[dict[str, object]]) -> ReceiptFile:
    return ReceiptFile(runs=tuple(_runs(records)), unreadable=0, malformed=0)


def _independent() -> list[dict[str, object]]:
    """Retries that ARE independent at p = 0.5: 32 runs, half recover at every depth."""
    records: list[dict[str, object]] = []
    records += [_receipt([True], task=f"a{i}") for i in range(16)]
    records += [_receipt([False, True], task=f"b{i}") for i in range(8)]
    records += [_receipt([False, False, True], task=f"c{i}") for i in range(4)]
    records += [_receipt([False, False, False], task=f"d{i}") for i in range(4)]
    return records


def _dependent() -> list[dict[str, object]]:
    """Second attempts recover at the first-attempt rate; third attempts recover nothing at all."""
    records: list[dict[str, object]] = []
    records += [_receipt([True], task=f"a{i}") for i in range(20)]
    records += [_receipt([False, True], task=f"b{i}") for i in range(10)]
    records += [_receipt([False, False, False], task=f"c{i}") for i in range(10)]
    return records


# --- (i) the probe can say "no effect" ----------------------------------------------------------


def test_independent_retries_are_not_flagged() -> None:
    """At p = 0.5 on every row the tails sit near the middle and nothing is flagged."""
    rows = conditional_rows(_runs(_independent()))
    assert [(r.index, r.n, r.succeeded) for r in rows] == [(1, 32, 16), (2, 16, 8), (3, 8, 4)]
    assert rows[0].tail is None and rows[0].p_prev is None
    assert rows[1].p_prev == pytest.approx(0.5) and rows[1].expected == pytest.approx(8.0)
    assert rows[1].tail == pytest.approx(0.5982, abs=1e-3)
    assert rows[2].tail == pytest.approx(0.6367, abs=1e-3)
    assert flags_contamination(rows[1].tail) is False
    assert flags_contamination(rows[2].tail) is False
    assert rows[1].zero_needs is None and rows[2].zero_needs is None
    assert "BELOW IID" not in format_report(_file(_independent()))


def test_independent_retries_match_the_iid_pass_at_k() -> None:
    """When retries really are fresh draws, observed pass@k lands on 1 − (1 − p1)^k exactly."""
    runs = _runs(_independent())
    two = pass_at_k(runs, 2)
    three = pass_at_k(runs, 3)
    assert two.p1 == pytest.approx(0.5) and two.n1 == 32
    assert two.observed == pytest.approx(0.75) and two.iid == pytest.approx(0.75)
    assert three.observed == pytest.approx(0.875) and three.iid == pytest.approx(0.875)
    assert two.exhausted_before_k == 0 and two.cut_before_k == 0


# --- (ii) dependent retries are flagged ---------------------------------------------------------


def test_zero_recoveries_where_independence_predicts_five_are_flagged() -> None:
    rows = conditional_rows(_runs(_dependent()))
    third = rows[2]
    assert (third.n, third.succeeded, third.failed) == (10, 0, 10)
    assert third.p_prev == pytest.approx(0.5) and third.expected == pytest.approx(5.0)
    assert third.tail == pytest.approx(0.5**10)
    assert flags_contamination(third.tail) is True
    # The second row recovers at the first row's rate, so it must NOT be flagged alongside.
    assert flags_contamination(rows[1].tail) is False
    # Power, stated: zero of five would already have been enough at p = 0.5.
    assert third.zero_needs == 5
    # And the counter-question: only a previous rate below ~0.26 would make 0/10 ordinary.
    assert third.p_prev_max_consistent == pytest.approx(1 - 0.05**0.1, abs=1e-3)
    report = format_report(_file(_dependent()))
    assert "BELOW IID" in report and "independence would need" in report


# --- (iii) the binomial tail, by hand -----------------------------------------------------------


def test_binomial_tail_matches_hand_values() -> None:
    assert binomial_tail(0, 11, 0.19) == pytest.approx(0.81**11)  # the plan's 0.098
    assert binomial_tail(1, 3, 0.5) == pytest.approx(0.5)  # {0,1} heads of 3 = 4/8
    assert binomial_tail(2, 4, 0.5) == pytest.approx(11 / 16)  # 1 + 4 + 6 of 16
    assert binomial_tail(5, 5, 0.3) == pytest.approx(1.0)
    assert binomial_tail(9, 5, 0.3) == pytest.approx(1.0)  # at_most beyond n clamps to n
    assert binomial_tail(0, 5, 0.0) == pytest.approx(1.0)
    assert binomial_tail(4, 5, 1.0) == pytest.approx(0.0)


def test_binomial_tail_refuses_what_it_cannot_compute() -> None:
    assert binomial_tail(0, 0, 0.5) is None
    assert binomial_tail(-1, 5, 0.5) is None
    assert binomial_tail(1, 5, 1.5) is None
    assert flags_contamination(None) is None
    assert flags_contamination(0.049) is True and flags_contamination(0.05) is False


def test_the_power_helpers_agree_with_the_tail() -> None:
    assert zero_successes_needed(0.19) == 15  # 0.81**14 = 0.052, 0.81**15 = 0.042
    assert zero_successes_needed(0.5) == 5
    assert zero_successes_needed(0.0) is None and zero_successes_needed(1.0) is None
    edge = max_rate_consistent(4, 21, 0.05)
    assert edge is not None
    at_edge = binomial_tail(4, 21, edge)
    past_edge = binomial_tail(4, 21, edge + 0.01)
    assert at_edge is not None and past_edge is not None
    assert at_edge >= 0.05 > past_edge
    assert max_rate_consistent(5, 5) == 1.0
    assert max_rate_consistent(0, 0) is None


# --- (iv) the money ---------------------------------------------------------------------------


def test_money_shares_sum_to_one_and_cut_attempts_are_priced_apart() -> None:
    runs = _runs(
        [
            _receipt([True], [1.0]),
            _receipt([False, True], [2.0, 0.5]),
            _receipt([False, False, False], [1.0, 0.25, 0.25], stopped_reason="spend"),
            {"ts": "", "task": "crashed before any attempt", "success": False, "attempts": []},
        ]
    )
    money = money_by_index(runs)
    assert money.total == pytest.approx(5.0)
    assert money.by_index == {1: 4.0, 2: 0.75, 3: 0.25}
    assert money.share is not None
    assert money.share[3] == pytest.approx(0.05) and money.share[2] == pytest.approx(0.15)
    assert sum(money.share.values()) == pytest.approx(1.0)
    assert money.cut_usd == pytest.approx(0.25)  # only the capped run's LAST attempt was cut
    assert money.runs_without_attempts == 1 and money.unpriced_attempts == 0


def test_one_unpriced_attempt_makes_the_total_none_not_smaller() -> None:
    runs = _runs([_receipt([True], [1.0]), _receipt([False, True], [2.0, None])])
    money = money_by_index(runs)
    assert money.total is None and money.share is None and money.by_index is None
    assert money.cut_usd is None and money.unpriced_attempts == 1
    assert "total: None" in format_report(_file([_receipt([False], [None])]))


# --- (v) missing fields are None or unreadable, never a number --------------------------------


def test_a_receipt_without_a_verdict_is_unreadable_not_a_failure(tmp_path: Path) -> None:
    no_success = _receipt([False])
    attempts = no_success["attempts"]
    assert isinstance(attempts, list)
    del attempts[0]["success"]
    assert parse_receipt(no_success) is None
    assert parse_receipt({"task": "x", "success": False}) is None  # no attempts list at all
    stringly = _receipt([True])
    stringly_attempts = stringly["attempts"]
    assert isinstance(stringly_attempts, list)
    stringly_attempts[0]["success"] = "true"
    assert parse_receipt(stringly) is None
    gap = _receipt([False, False])
    gap_attempts = gap["attempts"]
    assert isinstance(gap_attempts, list)
    gap_attempts[1]["index"] = 3
    assert parse_receipt(gap) is None

    path = tmp_path / "runs.jsonl"
    path.write_text(
        json.dumps(_receipt([False, True]))
        + "\n"
        + json.dumps(no_success)
        + "\n"
        + "{not json\n"
        + "\n",
        encoding="utf-8",
    )
    file = read_receipts(path)
    assert len(file.runs) == 1 and file.unreadable == 1 and file.malformed == 1
    # The unreadable run must not have leaked into the table as a failed first attempt.
    assert conditional_rows(file.runs)[0].n == 1


def test_rows_and_pass_at_k_report_none_where_nothing_was_observed() -> None:
    # A later attempt after a success is excluded from the conditional table, not counted.
    rows = conditional_rows(_runs([_receipt([True, True])]))
    assert rows[1].n == 0 and rows[1].rate is None
    assert rows[1].tail is None and rows[1].expected is None and rows[1].zero_needs is None
    empty = pass_at_k([], 2)
    assert empty.observed is None and empty.iid is None and empty.p1 is None
    assert empty.floor is None and empty.n1 == 0
    assert conditional_rows([]) == ()


def test_max_attempts_is_used_when_recorded_and_inferred_when_not() -> None:
    runs = _runs(
        [
            _receipt([False], max_attempts=1),  # allowed one: not eligible for pass@2
            _receipt([True], max_attempts=3),  # allowed three: eligible, passed
            _receipt([False]),  # no cap on the receipt, stopped failing: exhausted below 2
            _receipt([False]),  # a second one, so that swapping the two buckets is visible
            _receipt([False], stopped_reason="spend"),  # the ceiling stopped it: cut below 2
            _receipt([False, False]),  # reached attempt 2: eligible, failed
        ]
    )
    two = pass_at_k(runs, 2)
    assert two.recorded == 2 and two.inferred == 4
    assert two.eligible == 2 and two.passes == 1 and two.observed == pytest.approx(0.5)
    # Two exhausted, one cut — asymmetric on purpose: the first draft had one of each, and a
    # sabotage that swapped the buckets left it green.
    assert two.exhausted_before_k == 2 and two.cut_before_k == 1
    assert two.floor == pytest.approx(1 / 6)


def test_a_capped_run_marks_only_its_last_attempt_as_cut() -> None:
    run = parse_receipt(_receipt([False, False], stopped_reason="spend"))
    assert run is not None
    assert [a.cut for a in run.attempts] == [False, True] and run.capped
    by_ending = _receipt([False, False])
    by_ending["ending"] = "cancelled"
    run2 = parse_receipt(by_ending)
    assert run2 is not None and run2.attempts[1].cut and run2.capped
    row = conditional_rows([run])[1]
    assert row.n == 1 and row.cut == 1 and row.rate == 0.0
    assert row.rate_judged is None  # nothing was judged, so no judged rate — not 0%
