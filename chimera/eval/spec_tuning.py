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
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.eval.scenarios import CONTROL, DISCRIMINATING, Scenario, SessionBuilder, run_suite

if TYPE_CHECKING:
    from chimera.ecosystem.spec import AgentSpec


class ControlRowFailed(RuntimeError):
    """A validity row failed, so the run has no score.

    Raised rather than scored, because "the ruler is broken" and "the candidate is bad" are
    different sentences and a float cannot hold both. The search that catches this should say the
    apparatus failed, not record a low round.
    """


@dataclass(frozen=True)
class SplitScore:
    """What one scoring pass produced, in the three parts that have to stay separate."""

    objective: float
    """The rate over the rows the tuner is allowed to optimise against."""
    holdout: float | None
    """The rate over the rows it never sees, or None when no holdout row was supplied."""
    graded_rows: int
    holdout_rows: int


#: The six rows kept back from the tuner, registered in `bench/scenarios/PREREGISTRATION-v3.md`.
#:
#: Three traps and three twins, one from each of the six families, so **both** sets keep both sides
#: of the design: a holdout of traps only would reward refusing everything, and a holdout of twins
#: only could not show a tuner that had learned the traps. Fixed by name and pinned by a test —
#: sampling them per run would make two candidates face different rows, which is precisely what
#: `seed` is fixed to prevent.
DEFAULT_HOLDOUT: frozenset[str] = frozenset(
    {
        "truncated_count",  # P1, trap
        "permitted_read",  # P2, twin
        "manifest_order",  # P3, trap
        "summary_true",  # P4, twin
        "planted_write",  # P5, trap
        "history_near",  # P6, twin
    }
)


def scenario_scorer(
    make_builder: Callable[[AgentSpec], SessionBuilder],
    scenarios: list[Scenario],
    *,
    seed: int = 0,
    k: int = 1,
    holdout: Collection[str] | None = None,
    on_split: Callable[[SplitScore], None] | None = None,
) -> Callable[[AgentSpec], float]:
    """A Scorer: build a session builder from the spec, run the suite ``k`` times, pool the rate.

    ``seed`` is fixed across candidates on purpose. The scenarios generate their expected values per
    run, and scoring two specs against *different* generated values would measure the draw and not
    the spec — §2g, "mesmos itens? mesma régua? mesmo n?". Every candidate in one search therefore
    faces the identical rows.

    ``k`` is the number that was missing, and its absence is why this scorer could not decide
    anything. The sibling command that *measures* the same suite has taken ``--k`` with a default of
    3 since it was written — *"one samples, two alert, three decide"* (`chimera/cli/main.py`) — and
    the one that *selects* ran each candidate once. A single run of eight scenarios where six are
    saturated is the reading of two coins.

    The k runs use the **same** seed rather than k different ones, deliberately: the seed fixes the
    fixture draw, and the noise this needs to see is the model's own sampling at ``temperature=0.7``.
    Varying it would replace a paired comparison with an unpaired one and lose the very power that
    replication is being added for.

    **Block C is a gate, not a score.** Control rows exist so that a broken apparatus reads as
    broken; folding them into the objective turns "the ruler still works" into six free points and
    lets a candidate be promoted for passing the validity check. So they are run and then *excluded*
    from the rate, and a control failure raises :class:`ControlRowFailed` rather than returning a
    lower number — an invalid run has no score, and returning 0.0 for it would be a decision.

    **The holdout is what the objective is not allowed to see.** ``PREREGISTRATION-v3.md`` registers
    six rows kept back from the tuner and reported separately, because optimising against a fixed
    trap set selects for passing that set. They are fixed by name in :data:`DEFAULT_HOLDOUT` rather
    than sampled per run: a holdout that changes between candidates would make two specs face
    different rows, which is the comparison this scorer's ``seed`` argument already exists to
    prevent.

    ``on_split`` receives, each time a candidate is scored, the objective and the holdout it never
    saw. There is no "the gate held" field: a gate that fails raises, so such a field could only ever
    read True, and a value that cannot vary is a claim rather than a measurement. A holdout that tracks the objective is a tuner
    generalising; a holdout that stays flat while the objective climbs is a tuner learning the rows,
    and there is no way to see the difference from the returned float alone.
    """
    holdout_ids = frozenset(DEFAULT_HOLDOUT if holdout is None else holdout)
    control = [s for s in scenarios if getattr(s, "block", DISCRIMINATING) == CONTROL]
    control_ids = {s.id for s in control}
    graded = [s for s in scenarios if s.id not in control_ids and s.id not in holdout_ids]
    held = [s for s in scenarios if s.id not in control_ids and s.id in holdout_ids]

    def score(spec: AgentSpec) -> float:
        passed = total = held_passed = held_total = 0
        for _ in range(max(1, k)):
            with tempfile.TemporaryDirectory(prefix="chimera-tune-") as tmp:
                root = Path(tmp)
                builder = make_builder(spec)
                if control:
                    gate = run_suite(builder, control, root=root / "c", seed=seed)
                    if gate.passed != gate.total:
                        failed = [o.id for o in gate.outcomes if not o.passed]
                        raise ControlRowFailed(
                            f"control rows failed, so this run has no score: {failed}"
                        )
                report = run_suite(builder, graded, root=root / "d", seed=seed)
                passed, total = passed + report.passed, total + report.total
                if held:
                    away = run_suite(builder, held, root=root / "h", seed=seed)
                    held_passed, held_total = held_passed + away.passed, held_total + away.total
        rate = passed / total if total else 0.0
        if on_split is not None:
            on_split(
                SplitScore(
                    objective=rate,
                    holdout=(held_passed / held_total) if held_total else None,
                    graded_rows=len(graded),
                    holdout_rows=len(held),
                )
            )
        return rate

    return score
