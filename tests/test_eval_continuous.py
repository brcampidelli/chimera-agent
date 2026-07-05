"""Tests for the continuous-evolution benchmark harness."""

from __future__ import annotations

from chimera.eval import EvalTask, demo_tasks, run_continuous, run_evolution


def _tasks(n: int) -> list[EvalTask]:
    return [EvalTask(f"t{i}", "p", lambda o: o == "ok") for i in range(n)]


def _keyed_tasks(n: int) -> list[EvalTask]:
    """Tasks with distinct prompts q0..q{n-1}, so a solver can fail specific ones."""
    return [EvalTask(f"t{i}", f"q{i}", lambda o: o == "ok") for i in range(n)]


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


# --- cost tracking (arXiv 2606.25519 token-inflation axis) ----------------------------


class CostingFixedSolver:
    """Returns a fixed output, reporting a per-call token cost from a schedule."""

    def __init__(self, output: str, costs: list[int]) -> None:
        self.output = output
        self.costs = costs
        self.i = -1

    def solve(self, prompt: str) -> str:
        return self.output

    def solve_with_cost(self, prompt: str) -> tuple[str, int | None]:
        self.i += 1
        return self.output, self.costs[self.i]


def test_cost_tracked_and_mean() -> None:
    report = run_continuous(CostingFixedSolver("ok", [10, 10, 30, 30]), _tasks(4))
    assert report.outcomes[0].cost == 10
    assert report.mean_cost() == 20.0


def test_cost_drift_surfaces_inflation() -> None:
    # first-half mean 10, second-half mean 30 → drift +20 (cost inflating within the run)
    report = run_continuous(CostingFixedSolver("ok", [10, 10, 30, 30]), _tasks(4))
    assert report.cost_drift() == 20.0
    summary = report.summary()
    assert summary["mean_cost"] == 20.0 and summary["cost_drift"] == 20.0


def test_no_cost_keys_when_solver_reports_none() -> None:
    report = run_continuous(FixedSolver("ok"), _tasks(4))
    assert report.mean_cost() is None
    assert "mean_cost" not in report.summary()


# --- multi-round evolution: stagnation (vector mode) + cost trend ---------------------


class KeyedSolver:
    """Passes prompts in ``good``, fails the rest — the same way every round."""

    def __init__(self, good: set[str]) -> None:
        self.good = good

    def solve(self, prompt: str) -> str:
        return "ok" if prompt in self.good else "bad"


def test_run_evolution_flags_stagnation_when_same_tasks_fail() -> None:
    tasks = _keyed_tasks(6)
    solver = KeyedSolver(good={"q0", "q1", "q2"})  # q3,q4,q5 fail EVERY round
    report = run_evolution(solver, tasks, rounds=3)
    assert report.stagnation is not None and report.stagnation.stagnant
    assert report.summary()["stagnant"] == 1.0
    assert report.summary()["rounds"] == 3.0


def test_run_evolution_not_stagnant_when_all_pass() -> None:
    # all rounds pass everything → identical all-zero vectors but NO persistent failures
    report = run_evolution(FixedSolver("ok"), _keyed_tasks(6), rounds=3)
    assert report.stagnation is not None and not report.stagnation.stagnant


def test_run_evolution_cost_trend_across_rounds() -> None:
    class RisingCost:
        def __init__(self) -> None:
            self.calls = 0

        def solve(self, prompt: str) -> str:
            return "ok"

        def solve_with_cost(self, prompt: str) -> tuple[str, int | None]:
            round_index = self.calls // 4  # 4 tasks per round
            self.calls += 1
            return "ok", 10 * (round_index + 1)  # round0=10, round1=20, round2=30

    report = run_evolution(RisingCost(), _keyed_tasks(4), rounds=3)
    assert report.cost_trend() == 20.0  # last round mean 30 − first round mean 10
    assert report.summary()["cost_trend"] == 20.0
