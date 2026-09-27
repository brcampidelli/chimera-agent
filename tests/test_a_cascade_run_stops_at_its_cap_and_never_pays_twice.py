"""The verified-cascade harness's money rules: the admission stop, resumption, and halts.

Amendment 0 set the cap at US$ 20 with admission stopping at US$ 18. What has to stay true:

* **a paid call is admitted only while spend plus its estimate stays under the stop**, and a stage
  that meets the stop raises before the call is made — never after;
* **the ledger is the log**: spend is re-read from ``calls.jsonl``, so a restarted run knows what it
  already paid;
* **a call that succeeded, or halted after its one fresh re-run, is never made again** (resumable by
  key), and a halt the call itself declares (an empty answer after its re-ask) is not re-run;
* **a rate-limit failure is counted apart and re-queued once** (PROTOCOL §2, Amendment 5 of
  bench/default_model).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.verified_cascade import harness  # noqa: E402
from bench.verified_cascade.harness import BudgetExhausted, CallLog, Ledger, run_call  # noqa: E402


def test_the_cap_and_the_admission_stop_are_the_owners() -> None:
    assert harness.CAP_USD == 20.00
    assert harness.ADMISSION_STOP_USD == 18.00


def test_admission_stops_before_the_call_that_would_cross_the_stop(tmp_path: Path) -> None:
    log = CallLog(tmp_path / "calls.jsonl")
    log.append({"key": "earlier", "kind": "draft_sol", "status": "ok", "usd": 17.995})
    ledger = Ledger(log)
    made: list[str] = []

    def fn() -> dict[str, float]:
        made.append("called")
        return {"usd": 0.013}

    with pytest.raises(BudgetExhausted):
        run_call(log, ledger, "next", "draft_sol", {}, fn)
    assert made == [], "the call was made before admission was checked"
    # A free call (the local verifier) is always admitted.
    run_call(log, ledger, "local", "local", {}, lambda: {"usd": 0.0, "choice": "supported"})
    assert log.done("local")


def test_the_estimate_becomes_the_measured_mean_once_calls_exist(tmp_path: Path) -> None:
    log = CallLog(tmp_path / "calls.jsonl")
    ledger = Ledger(log)
    assert ledger.estimate("draft_luna") == harness.ESTIMATE_USD["draft_luna"]
    log.append({"key": "a", "kind": "draft_luna", "status": "ok", "usd": 0.002})
    log.append({"key": "b", "kind": "draft_luna", "status": "ok", "usd": 0.004})
    assert ledger.estimate("draft_luna") == pytest.approx(0.003)


def test_spend_is_read_back_from_the_log_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    log = CallLog(path)
    run_call(log, Ledger(log), "k1", "draft_luna", {}, lambda: {"usd": 0.25})
    run_call(log, Ledger(log), "k2", "grade", {}, lambda: {"usd": 0.5})
    again = CallLog(path)
    assert again.spent == pytest.approx(0.75)
    assert again.done("k1") and again.done("k2")


def test_a_done_call_is_not_made_again(tmp_path: Path) -> None:
    log = CallLog(tmp_path / "calls.jsonl")
    calls = []

    def fn() -> dict[str, float]:
        calls.append(1)
        return {"usd": 0.01}

    run_call(log, Ledger(log), "k", "grade", {}, fn)
    run_call(CallLog(tmp_path / "calls.jsonl"), Ledger(log), "k", "grade", {}, fn)
    assert len(calls) == 1


def test_an_error_is_rerun_once_fresh_and_a_second_failure_is_a_halt(tmp_path: Path) -> None:
    log = CallLog(tmp_path / "calls.jsonl")
    attempts = []

    def fails() -> dict[str, float]:
        attempts.append(1)
        raise RuntimeError("boom")

    row = run_call(log, Ledger(log), "k", "grade", {}, fails)
    assert row["status"] == "halt" and len(attempts) == 2
    run_call(log, Ledger(log), "k", "grade", {}, fails)
    assert len(attempts) == 2, "a halted call was asked again"

    flaky = iter([RuntimeError("once"), None])

    def recovers() -> dict[str, float]:
        err = next(flaky)
        if err:
            raise err
        return {"usd": 0.001}

    row = run_call(log, Ledger(log), "k2", "grade", {}, recovers)
    assert row["status"] == "ok" and row["attempt"] == 2


def test_a_declared_halt_is_final_and_keeps_its_cost(tmp_path: Path) -> None:
    log = CallLog(tmp_path / "calls.jsonl")
    attempts = []

    def empty() -> dict[str, object]:
        attempts.append(1)
        return {"text": "", "usd": 0.001, "halt": "empty after the re-ask"}

    row = run_call(log, Ledger(log), "k", "draft_luna", {}, empty)
    assert row["status"] == "halt" and len(attempts) == 1
    assert log.spent == pytest.approx(0.001)


def test_a_rate_limit_is_requeued_once_and_counted_apart(tmp_path: Path) -> None:
    log = CallLog(tmp_path / "calls.jsonl")
    queue: list = []
    outcomes = iter([RuntimeError("HTTP 429 Too Many Requests"), None])

    def fn() -> dict[str, float]:
        err = next(outcomes)
        if err:
            raise err
        return {"usd": 0.001}

    row = run_call(log, Ledger(log), "k", "grade", {}, fn, requeue=queue)
    assert row["status"] == "rate_limited" and len(queue) == 1
    assert not log.done("k")
    queue.pop()()
    assert log.get("k")["status"] == "ok"
    lines = [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["status"] for x in lines] == ["rate_limited", "ok"]
