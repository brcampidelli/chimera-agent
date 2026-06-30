"""Score an :class:`AgentSpec` against the daily scenarios — the OpenJarvis tuning scorer.

Turns the right-hand scenario suite into a :data:`Scorer` for ``search_spec``: build a
solver from a candidate spec, run the scenarios, and return the pass rate. ``make_solver``
is injected so the scoring logic is testable without a model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from chimera.eval.scenarios import Scenario, run_scenarios

if TYPE_CHECKING:
    from chimera.ecosystem.spec import AgentSpec
    from chimera.eval.continuous import Solver


def scenario_scorer(
    make_solver: Callable[[AgentSpec], Solver], scenarios: list[Scenario]
) -> Callable[[AgentSpec], float]:
    """A Scorer: build a solver from the spec, run the scenarios, return the pass rate."""

    def score(spec: AgentSpec) -> float:
        return run_scenarios(make_solver(spec), scenarios).pass_rate

    return score
