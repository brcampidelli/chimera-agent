"""Continuous-evolution benchmark — the anti-EvoClaw proof.

The "Agentic Software" paper reports agents dropping from >80% on isolated tasks to
~38% over *continuous* evolution (long-horizon context + error propagation). This
harness runs a chain of tasks through a solver and measures whether performance
*holds*: overall pass rate, first-half vs second-half pass rate (the degradation
signal), and the longest passing streak.

The harness is the deliverable; ``demo_tasks`` is a tiny illustrative set. A real
suite would chain stateful tasks against an evolving repo.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from chimera.eval.anytime import Z95, proportion_diff_ci
from chimera.providers.gateway import Message, SupportsComplete


@dataclass
class EvalTask:
    """One benchmark task: a prompt plus a check on the output."""

    id: str
    prompt: str
    check: Callable[[str], bool]


class Solver(Protocol):
    """Anything that can produce an answer string for a prompt."""

    def solve(self, prompt: str) -> str: ...


@dataclass
class TaskOutcome:
    id: str
    passed: bool
    output: str = ""


@dataclass
class EvolutionReport:
    """Results and degradation metrics for a continuous run."""

    outcomes: list[TaskOutcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def half_rates(self) -> tuple[float, float]:
        if self.total < 2:
            return self.pass_rate, self.pass_rate
        mid = self.total // 2
        first = self.outcomes[:mid]
        second = self.outcomes[mid:]
        first_rate = sum(o.passed for o in first) / len(first)
        second_rate = sum(o.passed for o in second) / len(second)
        return first_rate, second_rate

    @property
    def degradation(self) -> float:
        """First-half minus second-half pass rate. Positive == degraded."""
        first, second = self.half_rates()
        return first - second

    def _halves(self) -> tuple[list[TaskOutcome], list[TaskOutcome]]:
        mid = self.total // 2
        return self.outcomes[:mid], self.outcomes[mid:]

    def degradation_ci(self, z: float = Z95) -> tuple[float, float]:
        """Confidence interval on the degradation (first-half minus second-half pass rate).

        A point ``degradation`` of 0.2 on a short chain is usually noise; this band says
        how much of it is real. A lower bound > 0 means the second half is significantly
        worse — an anytime-honest degradation signal instead of a bare subtraction.
        """
        first, second = self._halves()
        if not first or not second:
            return (0.0, 0.0)
        s1 = sum(1 for o in first if o.passed)
        s2 = sum(1 for o in second if o.passed)
        return proportion_diff_ci(s1, len(first), s2, len(second), z)

    def degraded_significantly(self, z: float = Z95, min_n: int = 8) -> bool | None:
        """True if degradation is statistically significant; None if the sample is too small.

        Guarded by ``min_n`` per half — below it the interval is uselessly wide, so we
        return None ('cannot say') rather than a false negative.
        """
        first, second = self._halves()
        if len(first) < min_n or len(second) < min_n:
            return None
        return self.degradation_ci(z)[0] > 0.0

    @property
    def longest_pass_streak(self) -> int:
        best = current = 0
        for outcome in self.outcomes:
            current = current + 1 if outcome.passed else 0
            best = max(best, current)
        return best

    def summary(self) -> dict[str, float]:
        first, second = self.half_rates()
        ci_low, ci_high = self.degradation_ci()
        sig = self.degraded_significantly()
        return {
            "total": float(self.total),
            "pass_rate": round(self.pass_rate, 3),
            "first_half": round(first, 3),
            "second_half": round(second, 3),
            "degradation": round(self.degradation, 3),
            "degradation_ci_low": round(ci_low, 3),
            "degradation_ci_high": round(ci_high, 3),
            # 1.0 = significantly degraded, 0.0 = not, -1.0 = sample too small to say
            "degraded_significant": 1.0 if sig else (0.0 if sig is False else -1.0),
            "longest_streak": float(self.longest_pass_streak),
        }


def run_continuous(
    solver: Solver,
    tasks: Iterable[EvalTask],
    *,
    on_task: Callable[[TaskOutcome], None] | None = None,
) -> EvolutionReport:
    """Run each task through ``solver`` in order, recording pass/fail."""
    report = EvolutionReport()
    for task in tasks:
        try:
            output = solver.solve(task.prompt)
            passed = bool(task.check(output))
        except Exception as exc:  # a crashing task counts as a failure, never aborts
            output, passed = f"error: {exc}", False
        outcome = TaskOutcome(id=task.id, passed=passed, output=output)
        report.outcomes.append(outcome)
        if on_task is not None:
            on_task(outcome)
    return report


class SingleModelSolver:
    """A solver that answers each prompt with one model call."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def solve(self, prompt: str) -> str:
        return self.backend.complete(
            [Message(role="user", content=prompt)], model=self.model, temperature=0.0
        ).content


def demo_tasks() -> list[EvalTask]:
    """A tiny illustrative task set (sanity checks, not a real evolution chain)."""
    return [
        EvalTask("add", "What is 2+2? Reply with only the number.", lambda o: "4" in o),
        EvalTask("capital", "Reply with only the capital of France.", lambda o: "paris" in o.lower()),
        EvalTask("reverse", "Reverse the word 'cat'. Reply with only the result.", lambda o: "tac" in o.lower()),
        EvalTask("count", "How many letters in 'hello'? Reply with only the number.", lambda o: "5" in o),
        EvalTask("upper", "Uppercase the word 'go'. Reply with only the result.", lambda o: "GO" in o),
        EvalTask("max", "What is the larger of 7 and 3? Reply with only the number.", lambda o: "7" in o),
        EvalTask("mult", "What is 6 times 7? Reply with only the number.", lambda o: "42" in o),
        EvalTask("first", "Reply with only the first letter of the word 'chimera'.", lambda o: "c" in o.lower()),
        EvalTask("bool", "Is 10 greater than 2? Reply with only 'yes' or 'no'.", lambda o: "yes" in o.lower()),
        EvalTask("even", "Is 4 even? Reply with only 'yes' or 'no'.", lambda o: "yes" in o.lower()),
    ]
