"""Tests for the stateful chained continuous-evolution benchmark."""

from __future__ import annotations

import re

from chimera.eval import ChainStep, demo_chain, run_chain


def _number_in(prompt: str) -> int:
    match = re.search(r"number (\d+)", prompt)
    return int(match.group(1)) if match else 0


class IncrementSolver:
    def solve(self, prompt: str) -> str:
        return str(_number_in(prompt) + 1)


class DegradingIncrementSolver:
    """Increments correctly for ``good`` steps, then stops (corrupting the state)."""

    def __init__(self, good: int) -> None:
        self.good = good
        self.calls = 0

    def solve(self, prompt: str) -> str:
        self.calls += 1
        n = _number_in(prompt)
        return str(n + 1) if self.calls <= self.good else str(n)


def test_correct_solver_passes_whole_chain() -> None:
    report = run_chain(IncrementSolver(), demo_chain(8), initial_state="0")
    assert report.pass_rate == 1.0
    assert report.degradation == 0.0
    assert report.longest_pass_streak == 8


def test_error_propagates_through_chain() -> None:
    report = run_chain(DegradingIncrementSolver(good=3), demo_chain(8), initial_state="0")
    passed = [o.passed for o in report.outcomes]
    assert passed[:3] == [True, True, True]
    assert all(not p for p in passed[3:])  # corrupted state fails every later step
    assert report.passed == 3
    assert report.degradation > 0


def test_chain_handles_solver_exception() -> None:
    class Boom:
        def solve(self, prompt: str) -> str:
            raise RuntimeError("kaboom")

    report = run_chain(Boom(), demo_chain(3), initial_state="0")
    assert report.passed == 0


def test_custom_chain_accumulates_state() -> None:
    steps = [
        ChainStep("a", render=lambda s: f"add A to {s!r}", integrate=lambda s, o: s + "A", check=lambda s: s == "A"),
        ChainStep("b", render=lambda s: f"add B to {s!r}", integrate=lambda s, o: s + "B", check=lambda s: s == "AB"),
    ]

    class Dummy:
        def solve(self, prompt: str) -> str:
            return "ok"

    report = run_chain(Dummy(), steps, initial_state="")
    assert report.pass_rate == 1.0
    assert report.outcomes[-1].output == "AB"
