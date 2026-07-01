"""Deterministic tests for the hard benchmark suites (no network).

The chain tests use an OracleSolver that applies each correct operation to the state it
reads from the prompt — so when it 'forgets' one step, the corrupted state propagates
exactly as a real model's mistake would, encoding the EvoClaw collapse as a fixed test.
"""

from __future__ import annotations

import re

from chimera.eval import (
    HARD_CHAIN_OPS,
    HARD_CHAIN_START,
    hard_chain,
    hard_tasks,
    run_chain,
)


class OracleSolver:
    """Applies the correct op to the state parsed from the prompt; optionally skips one."""

    def __init__(self, break_at: int | None = None) -> None:
        self.ops = [op for _, _, op in HARD_CHAIN_OPS]
        self.break_at = break_at
        self.i = 0

    def solve(self, prompt: str) -> str:
        state = int(re.findall(r"-?\d+", prompt.replace(",", ""))[0])
        op = self.ops[self.i]
        self.i += 1
        if self.break_at is not None and self.i == self.break_at:
            return str(state)  # 'forgot' to apply the op -> wrong, and it will propagate
        return str(op(state))


def test_hard_tasks_checks_accept_correct_reject_wrong() -> None:
    tasks = {t.id: t for t in hard_tasks()}
    assert len(tasks) == 12
    assert tasks["bat_ball"].check("5") and not tasks["bat_ball"].check("10")
    assert tasks["look_say"].check("312211") and not tasks["look_say"].check("111221")
    assert tasks["sister_age"].check("67") and not tasks["sister_age"].check("35")
    assert tasks["days"].check("Wednesday") and not tasks["days"].check("Tuesday")
    assert tasks["feathers"].check("same") and not tasks["feathers"].check("bricks")


def test_hard_chain_perfect_run_holds() -> None:
    report = run_chain(OracleSolver(), hard_chain(), initial_state=HARD_CHAIN_START)
    summary = report.summary()
    assert summary["pass_rate"] == 1.0
    assert summary["degradation"] == 0.0
    assert report.outcomes[-1].output == "185"  # 17 -> ... -> 185


def test_hard_chain_error_propagates_collapses_second_half() -> None:
    # 'forget' the 5th step (digit-sum x 11): it fails and corrupts the running state,
    # so every later step — computed correctly from a wrong number — also fails.
    report = run_chain(OracleSolver(break_at=5), hard_chain(), initial_state=HARD_CHAIN_START)
    summary = report.summary()
    assert summary["first_half"] == 1.0  # steps 1-4 pass
    assert summary["second_half"] == 0.0  # steps 5-8 all fail (propagation)
    assert summary["degradation"] == 1.0  # the maximal EvoClaw collapse
    assert summary["pass_rate"] == 0.5
