"""Score an :class:`AgentSpec` against the daily scenarios — the OpenJarvis tuning scorer.

Turns the right-hand scenario suite into a :data:`Scorer` for ``search_spec``: build a session
builder from a candidate spec, run the suite, and return the pass rate. ``make_builder`` is injected
so the scoring logic is testable without a model.

The suite is now driven through a real :class:`~chimera.interface.session.ChatSession`, so this
scorer and ``chimera scenarios`` measure the *same* thing. They used to be two rulers under one
name — this one built an ``Agent`` with tools, that one a bare single-model call — and a
non-regression criterion read against a ruler at the ceiling could only ever tie.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.eval.scenarios import Scenario, SessionBuilder, run_suite

if TYPE_CHECKING:
    from chimera.ecosystem.spec import AgentSpec


def scenario_scorer(
    make_builder: Callable[[AgentSpec], SessionBuilder],
    scenarios: list[Scenario],
    *,
    seed: int = 0,
) -> Callable[[AgentSpec], float]:
    """A Scorer: build a session builder from the spec, run the suite, return the pass rate.

    ``seed`` is fixed across candidates on purpose. The scenarios generate their expected values per
    run, and scoring two specs against *different* generated values would measure the draw and not
    the spec — §2g, "mesmos itens? mesma régua? mesmo n?". Every candidate in one search therefore
    faces the identical eight tasks.
    """

    def score(spec: AgentSpec) -> float:
        with tempfile.TemporaryDirectory(prefix="chimera-tune-") as tmp:
            report = run_suite(make_builder(spec), scenarios, root=Path(tmp), seed=seed)
        return report.pass_rate

    return score
