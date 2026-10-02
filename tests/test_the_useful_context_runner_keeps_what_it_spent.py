"""The useful-context runner writes down every call it paid for, including the ones after its stop rule.

On 2026-09-30 the option-A run of `bench/useful_context` (glm-5.3-flash) was ended by its stop rule and
printed US$ 0.3166, over a results file whose rows summed to US$ 0.1811. Leaving the thread pool waits
for the items already running; they were sent and paid for, their cost stayed in the runner's counter,
and their rows were dropped. A published amendment then quoted the printed number as "recorded", which
no file supported. The runner now keeps those rows beside the analysed ones (never among them) and
writes its own count, so the artifact accounts for the whole spend.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

RUN = Path(__file__).resolve().parents[1] / "bench" / "useful_context" / "run.py"


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location("useful_context_run_under_test", RUN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "useful_context_run_under_test", module)
    spec.loader.exec_module(module)
    module._spent = 0.0
    return module


def _fake_one(runner: Any, slow_after: int) -> Any:
    """Every call errors (so the stop rule fires after three items) and costs a cent. Items past the
    third take longer, so at least one is still running when the rule fires."""
    lock = threading.Lock()

    def one(item: Any, length: int, arm: str, cpt: float, cap: float) -> dict[str, Any]:
        if int(item.id[1:]) >= slow_after:
            time.sleep(0.05)
        with lock:
            runner._spent += 0.01
        return {
            "item": item.id,
            "arm": arm,
            "length": length,
            "error": "boom",
            "cost": 0.01,
            "grade": {"ok": False, "kind": "error"},
            "prompt_tokens": 0,
        }

    return one


def test_rows_finished_after_the_stop_rule_are_kept_apart_and_the_spend_adds_up(
    runner: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_one", _fake_one(runner, slow_after=3))
    plan = [(item, [(1000 * k, str(k)) for k in range(10)]) for item in runner.it.items("T", 12)]
    out = tmp_path / "run.json"

    runner.execute(plan, 4.0, 100.0, 2, out, {"phase": "test"})

    data = json.loads(out.read_text(encoding="utf-8"))
    after = data.get("after_stop_rows", [])
    assert len(data["rows"]) == 30, "the stop rule fires after three items, as registered"
    assert after, (
        "an item that was running when the rule fired was paid for and must be in the file"
    )
    assert not {r["item"] for r in after} & {r["item"] for r in data["rows"]}, (
        "kept apart, never analysed"
    )
    total = sum(r["cost"] for r in data["rows"]) + sum(r["cost"] for r in after)
    assert data["runner_usd"] == pytest.approx(total), (
        "the file accounts for everything the runner counted"
    )
    assert data["usd"] == pytest.approx(sum(r["cost"] for r in data["rows"])), (
        "`usd` stays the analysed rows"
    )


def test_a_run_that_finishes_has_no_after_stop_rows_and_still_says_what_it_spent(
    runner: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_one", _fake_one(runner, slow_after=99))
    plan = [(item, [(1000, "1")]) for item in runner.it.items("T", 3)]
    out = tmp_path / "run.json"

    runner.execute(plan, 4.0, 100.0, 2, out, {"phase": "test"})

    data = json.loads(out.read_text(encoding="utf-8"))
    assert "after_stop_rows" not in data
    assert data["runner_usd"] == pytest.approx(0.03)
