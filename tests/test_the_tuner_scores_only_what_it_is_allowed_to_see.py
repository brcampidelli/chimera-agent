"""The tuning objective excludes the validity gate and the holdout, and says so per round.

Two holes were written into `scenario_scorer`'s own docstring rather than fixed, and this closes
both.

**Block C was worth points.** The scorer averaged over every row it was handed, so passing the whole
v3 suite folded six *control* rows — a validity gate whose expected reading is 100% — into the
objective. Six free points, and a candidate could be promoted for passing the check that exists to
say whether the ruler still works.

**And the registered holdout did not exist.** `bench/scenarios/PREREGISTRATION-v3.md` registers six
rows kept back from the tuner and reported separately, because optimising against a fixed trap set
selects for passing that set. Nothing implemented it, so every row was optimised against.

Neither is about the v3 rows in particular: any suite handed to this scorer had its control rows
scored and no holdout withheld.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.eval.scenarios import CONTROL, DISCRIMINATING, Scenario, ScenarioTurn
from chimera.eval.spec_tuning import (
    DEFAULT_HOLDOUT,
    ControlRowFailed,
    SplitScore,
    scenario_scorer,
)


def _row(name: str, block: str, *, family: str = "", passes: bool = True) -> Scenario:
    """A scenario that passes or fails by construction — no model, no fixture, no ambiguity."""
    return Scenario(
        id=name,
        block=block,
        family=family,
        turns=(ScenarioTurn("say anything"),),
        check=lambda _ctx, _p=passes: {"ok": _p},
        asserts=f"{name} is wired to {'pass' if passes else 'fail'}",
    )


class _Spec:
    """Stands in for `AgentSpec`; the scorer only ever hands it to `make_builder`."""


def _score(rows: list[Scenario], **kwargs: Any) -> tuple[float, list[SplitScore]]:
    """Score `rows` with the fake session builder this suite already ships.

    The rows decide their own outcome, so the agent behind them is irrelevant — what is under test
    is which rows reach the average, not what any of them answers.
    """
    from tests.test_scenarios import OracleAgent, _builder

    seen: list[SplitScore] = []
    scorer = scenario_scorer(lambda _spec: _builder(OracleAgent), rows, on_split=seen.append, **kwargs)
    return scorer(_Spec()), seen  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the gate


def test_control_rows_are_not_worth_points() -> None:
    """Six control rows that pass must not raise the objective by a single point.

    Written as a comparison rather than as an absolute: the same discriminating rows scored with and
    without control rows beside them have to produce the identical number.
    """
    graded = [_row("d1", DISCRIMINATING, passes=True), _row("d2", DISCRIMINATING, passes=False)]
    alone, _ = _score(graded)
    with_control, split = _score([*graded, _row("c1", CONTROL), _row("c2", CONTROL)])

    assert alone == with_control == 0.5
    assert split[0].graded_rows == 2, "the control rows must not be in the scored set"


def test_a_failing_control_row_has_no_score_rather_than_a_low_one() -> None:
    """"The ruler is broken" and "the candidate is bad" are different sentences, and a float cannot
    hold both. Returning 0.0 here would be a decision dressed as a measurement."""
    rows = [_row("d1", DISCRIMINATING), _row("c1", CONTROL, passes=False)]
    with pytest.raises(ControlRowFailed, match="c1"):
        _score(rows)


# --------------------------------------------------------------------------- the holdout


def test_the_holdout_is_excluded_from_the_objective() -> None:
    rows = [
        _row("d1", DISCRIMINATING, passes=True),
        _row("held", DISCRIMINATING, passes=False),
    ]
    rate, split = _score(rows, holdout={"held"})

    assert rate == 1.0, "the withheld row must not drag the objective"
    assert split[0].graded_rows == 1
    assert split[0].holdout_rows == 1


def test_the_holdout_is_reported_beside_the_objective_not_inside_it() -> None:
    """The number that separates a tuner generalising from a tuner learning the rows."""
    rows = [
        _row("d1", DISCRIMINATING, passes=True),
        _row("held", DISCRIMINATING, passes=False),
    ]
    _, split = _score(rows, holdout={"held"})

    assert split[0].objective == 1.0
    assert split[0].holdout == 0.0


def test_no_holdout_row_reports_none_rather_than_zero() -> None:
    """`None` and `0.0` are different facts: nothing withheld, against everything withheld failed."""
    _, split = _score([_row("d1", DISCRIMINATING)], holdout=set())
    assert split[0].holdout is None


# --------------------------------------------------------------------------- what is withheld


def test_the_holdout_is_fixed_by_name_and_spans_every_family() -> None:
    """Sampled per run, the holdout would make two candidates face different rows — which is exactly
    what this scorer's fixed `seed` exists to prevent.

    Three traps and three twins: a holdout of traps only would reward refusing everything, and one of
    twins only could not show a tuner that had learned the traps.
    """
    from chimera.eval.scenarios import daily_scenarios

    rows = {s.id: s for s in daily_scenarios()}
    assert len(DEFAULT_HOLDOUT) == 6
    missing = DEFAULT_HOLDOUT - set(rows)
    assert not missing, f"the holdout names rows that do not exist: {sorted(missing)}"

    families = {rows[i].family for i in DEFAULT_HOLDOUT}
    assert len(families) == 6, f"the holdout must span six families, got {sorted(families)}"
    assert all(rows[i].block == DISCRIMINATING for i in DEFAULT_HOLDOUT), (
        "a control row in the holdout would withhold the validity gate, not the objective"
    )


def test_an_unlabelled_row_is_scored_and_not_a_gate() -> None:
    """The default that this change had to invert, pinned so it cannot drift back.

    `Scenario.block` defaulted to `CONTROL` until 2026-09-10 — so a row nobody labelled was a
    **validity gate**, and once the scorer began honouring the distinction, two long-standing tests
    that built their own scenarios turned every one of them into a gate: one scored 0.0 because
    nothing was left to grade, the other raised.

    The direction is the point. A control row is a claim ("this must always pass, and its failure
    invalidates the run"), and a claim is not something a row should acquire by omission.
    """
    row = Scenario(
        id="unlabelled",
        turns=(ScenarioTurn("hi"),),
        check=lambda _ctx: True,
        asserts="nobody said which block this belongs to",
    )
    assert row.block == DISCRIMINATING

    rate, split = _score([row])
    assert split[0].graded_rows == 1, "an unlabelled row must be scored, not treated as a gate"
    assert rate == 1.0


def test_every_control_row_says_so_out_loud() -> None:
    """The six are marked, not inherited from a default that could change under them again."""
    import inspect

    from chimera.eval import scenarios as module

    source = inspect.getsource(module)
    assert source.count("block=CONTROL") == 6, (
        "a control row that relies on the field default is a gate nobody declared"
    )


def test_the_default_holdout_leaves_the_objective_something_to_measure() -> None:
    """A holdout that swallowed the discriminating set would make the objective a control-row echo."""
    from chimera.eval.scenarios import daily_scenarios

    rows = daily_scenarios()
    graded = [
        s for s in rows if s.block == DISCRIMINATING and s.id not in DEFAULT_HOLDOUT
    ]
    assert len(graded) >= 12, f"only {len(graded)} rows left to optimise against"
