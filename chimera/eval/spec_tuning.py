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
    k: int = 1,
) -> Callable[[AgentSpec], float]:
    """A Scorer: build a session builder from the spec, run the suite ``k`` times, pool the rate.

    ``seed`` is fixed across candidates on purpose. The scenarios generate their expected values per
    run, and scoring two specs against *different* generated values would measure the draw and not
    the spec — §2g, "mesmos itens? mesma régua? mesmo n?". Every candidate in one search therefore
    faces the identical eight tasks.

    ``k`` is the number that was missing, and its absence is why this scorer could not decide
    anything. The sibling command that *measures* the same suite has taken ``--k`` with a default of
    3 since it was written — *"one samples, two alert, three decide"* (`chimera/cli/main.py`) — and
    the one that *selects* ran each candidate once. A single run of eight scenarios where six are
    saturated is the reading of two coins.

    The k runs use the **same** seed rather than k different ones, deliberately: the seed fixes the
    fixture draw, and the noise this needs to see is the model's own sampling at ``temperature=0.7``.
    Varying it would replace a paired comparison with an unpaired one and lose the very power that
    replication is being added for.
    """

    def score(spec: AgentSpec) -> float:
        passed = total = 0
        for _ in range(max(1, k)):
            with tempfile.TemporaryDirectory(prefix="chimera-tune-") as tmp:
                report = run_suite(make_builder(spec), scenarios, root=Path(tmp), seed=seed)
            passed += report.passed
            total += report.total
        return passed / total if total else 0.0

    return score
