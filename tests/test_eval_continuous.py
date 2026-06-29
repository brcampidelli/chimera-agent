"""Tests for the continuous-evolution benchmark harness."""

from __future__ import annotations

from chimera.eval import EvalTask, demo_tasks, run_continuous


def _tasks(n: int) -> list[EvalTask]:
    return [EvalTask(f"t{i}", "p", lambda o: o == "ok") for i in range(n)]


class FixedSolver:
    def __init__(self, output: str) -> None:
        self.output = output

    def solve(self, prompt: str) -> str:
        return self.output


class DegradingSolver:
    """Passes the first ``good`` tasks, then fails."""

    def __init__(self, good: int) -> None:
        self.good = good
        self.calls = 0

    def solve(self, prompt: str) -> str:
        self.calls += 1
        return "ok" if self.calls <= self.good else "bad"


def test_all_pass() -> None:
    report = run_continuous(FixedSolver("ok"), _tasks(4))
    assert report.pass_rate == 1.0
    assert report.degradation == 0.0
    assert report.longest_pass_streak == 4


def test_degradation_detected() -> None:
    report = run_continuous(DegradingSolver(good=2), _tasks(4))
    assert report.pass_rate == 0.5
    first, second = report.half_rates()
    assert first == 1.0 and second == 0.0
    assert report.degradation == 1.0


def test_longest_streak() -> None:
    # pass, pass, fail, pass -> longest streak 2
    class Pattern:
        def __init__(self) -> None:
            self.seq = ["ok", "ok", "bad", "ok"]
            self.i = -1

        def solve(self, prompt: str) -> str:
            self.i += 1
            return self.seq[self.i]

    report = run_continuous(Pattern(), _tasks(4))
    assert report.longest_pass_streak == 2


def test_solver_exception_is_failure() -> None:
    class Boom:
        def solve(self, prompt: str) -> str:
            raise RuntimeError("kaboom")

    report = run_continuous(Boom(), _tasks(2))
    assert report.passed == 0
    assert "error" in report.outcomes[0].output


def test_summary_and_demo_tasks() -> None:
    report = run_continuous(FixedSolver("ok"), _tasks(2))
    summary = report.summary()
    assert summary["pass_rate"] == 1.0
    assert "degradation" in summary
    assert len(demo_tasks()) >= 4


def test_on_task_callback() -> None:
    seen: list[str] = []
    run_continuous(FixedSolver("ok"), _tasks(3), on_task=lambda o: seen.append(o.id))
    assert seen == ["t0", "t1", "t2"]
