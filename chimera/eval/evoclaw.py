"""EvoClaw stress test — does performance *hold* over a long, stateful chain?

The "Agentic Software" finding: agents fall from >80% on isolated tasks to ~38%
over *continuous* evolution, driven by (1) long-horizon context management and
(2) error propagation. This module pits two regimes against the **same** task
chain and measures the degradation gap between them:

- :func:`run_naive` carries whatever the solver last produced forward (so errors
  *propagate*) and never re-checks — the failure mode.
- :func:`run_guarded` keeps the authoritative state *outside* the solver
  (externalized state, HORIZON-style) and, after each step, **verifies and on
  failure reverts to the last good state and retries** (generate-vs-verify +
  verify-or-revert). One bad step costs a retry instead of corrupting the rest.

Both return the shared :class:`~chimera.eval.continuous.EvolutionReport`, so the
degradation metrics line up for an apples-to-apples comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from chimera.eval.continuous import EvolutionReport, Solver, TaskOutcome


@dataclass
class EvoStep:
    """One step: render a prompt from state, fold the output back, validate it."""

    id: str
    render: Callable[[str], str]  # authoritative state -> prompt
    integrate: Callable[[str, str], str]  # (state, solver output) -> candidate state
    validate: Callable[[str, str], bool]  # (prev state, candidate) -> is it correct?


def run_naive(solver: Solver, steps: list[EvoStep], *, initial_state: str = "") -> EvolutionReport:
    """No countermeasures: the candidate state is carried forward even when wrong."""
    report = EvolutionReport()
    state = initial_state
    for step in steps:
        try:
            candidate = step.integrate(state, solver.solve(step.render(state)))
            ok = bool(step.validate(state, candidate))
        except Exception:
            candidate, ok = state, False
        report.outcomes.append(TaskOutcome(step.id, ok, candidate))
        state = candidate  # propagate, right or wrong
    return report


def run_guarded(
    solver: Solver,
    steps: list[EvoStep],
    *,
    initial_state: str = "",
    max_retries: int = 2,
) -> EvolutionReport:
    """Externalized state + verify-or-revert: each step retries from the last good
    state, so a bad step never corrupts the ones after it."""
    report = EvolutionReport()
    last_good = initial_state
    for step in steps:
        ok = False
        for _ in range(max_retries + 1):
            try:
                candidate = step.integrate(last_good, solver.solve(step.render(last_good)))
                if step.validate(last_good, candidate):
                    last_good, ok = candidate, True
                    break
            except Exception:
                pass  # failed attempt → revert is implicit (last_good unchanged), retry
        report.outcomes.append(TaskOutcome(step.id, ok, last_good))
    return report


@dataclass
class EvoComparison:
    """Naive vs guarded over the same chain."""

    naive: EvolutionReport
    guarded: EvolutionReport

    @property
    def degradation_gap(self) -> float:
        """How much *more* the naive regime degrades than the guarded one (>0 = guard helps)."""
        return round(self.naive.degradation - self.guarded.degradation, 3)


def compare(
    solver_factory: Callable[[], Solver],
    steps: list[EvoStep],
    *,
    initial_state: str = "",
    max_retries: int = 2,
) -> EvoComparison:
    """Run the same chain naively vs guarded.

    ``solver_factory`` builds a fresh solver per regime so a stateful solver's
    internal counters don't bleed from one run into the other.
    """
    return EvoComparison(
        naive=run_naive(solver_factory(), steps, initial_state=initial_state),
        guarded=run_guarded(
            solver_factory(), steps, initial_state=initial_state, max_retries=max_retries
        ),
    )


def counter_chain(length: int = 12) -> list[EvoStep]:
    """A counter chain (start state ``"0"``): step *i* must make the value equal *i*.

    The validator is absolute, so one bad step corrupts the running value — which
    cascades in the naive regime and is caught-and-retried in the guarded one.
    """
    steps: list[EvoStep] = []
    for i in range(1, length + 1):
        steps.append(
            EvoStep(
                id=f"step{i}",
                render=lambda s: f"Add 1 to the number {s}. Reply with only the resulting number.",
                integrate=lambda s, out: out.strip(),
                validate=(lambda expected: (lambda prev, cand: cand.strip() == str(expected)))(i),
            )
        )
    return steps
