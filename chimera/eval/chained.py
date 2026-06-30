"""Stateful, chained continuous-evolution benchmark.

The single-shot benchmark (:mod:`chimera.eval.continuous`) measures whether a solver
holds up across *independent* tasks. This module measures the harder, EvoClaw-style
case: a **chain** where each step transforms an accumulating state, so a mistake
*propagates* — a corrupted state is carried into every later step. That is exactly the
"error propagation + long-horizon context" failure mode Chimera is built to resist.

The harness is solver-agnostic and fully deterministic for tests; the same
:class:`~chimera.eval.continuous.EvolutionReport` metrics apply.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from chimera.eval.continuous import EvolutionReport, Solver, TaskOutcome


@dataclass
class ChainStep:
    """One step in a stateful chain.

    ``render`` builds the prompt from the current state, ``integrate`` folds the
    solver's output back into the state, and ``check`` validates the new state.
    """

    id: str
    render: Callable[[str], str]
    integrate: Callable[[str, str], str]
    check: Callable[[str], bool]


def run_chain(
    solver: Solver,
    steps: list[ChainStep],
    *,
    initial_state: str = "",
) -> EvolutionReport:
    """Run a stateful chain; the state (corrupted or not) carries across steps."""
    report = EvolutionReport()
    state = initial_state
    for step in steps:
        try:
            output = solver.solve(step.render(state))
            new_state = step.integrate(state, output)
            passed = bool(step.check(new_state))
        except Exception:  # a crashing step fails and freezes the state
            new_state, passed = state, False
        report.outcomes.append(TaskOutcome(id=step.id, passed=passed, output=new_state))
        state = new_state  # propagate, right or wrong
    return report


def demo_chain(length: int = 8) -> list[ChainStep]:
    """A counter chain: each step adds 1 to the running number (start state '0').

    A solver that stops incrementing corrupts the state, and every later step then
    fails too — a clean, deterministic demonstration of error propagation.
    """
    steps: list[ChainStep] = []
    for i in range(1, length + 1):
        steps.append(
            ChainStep(
                id=f"step{i}",
                render=lambda s: f"Add 1 to the number {s}. Reply with only the resulting number.",
                integrate=lambda s, out: out.strip(),
                check=(lambda expected: (lambda s: s.strip() == str(expected)))(i),
            )
        )
    return steps
