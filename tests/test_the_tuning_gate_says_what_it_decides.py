"""The spec meta-search promotes on evidence, and its table says what actually happened.

Two defects, and the second is what hid the first.

**It selected on noise.** `search_spec` promoted a candidate on `score > best_score` — a bare
fraction over eight scenarios, quantised in steps of 1/8. Six of those eight are saturated
(`bench/scenarios/RESULTS.md`), so the score is the reading of two coins, and the scorer ran the
suite **once**: `run_suite`'s own docstring says "Run every scenario once", while the sibling
command that merely *measures* the same suite has taken `--k` with a default of 3 since it was
written — *"one samples, two alert, three decide"*. k=3 in the command that measures, k=1 in the
command that selects.

**And the table said "kept" about the wrong field.** `accepted` is `>=`, the advance is `>`, and the
column headed "kept" printed `accepted` — so a tie, the most common outcome against a saturated
ruler, printed ✓ over an incumbent that had not moved.

The remedy was already in this repository with the measured number in its docstring:
`chimera/eval/anytime.py` records that *"best-of-3 accepted a candidate whose true pass rate was 0.3
51.8% of the time"*. It is imported by `bench_ab`, `continuous`, `paired`, `auto_evolve` and
`collective` — every gate except this one.

**None of this makes the gate work.** On the published numbers it promotes nothing, which is the
honest answer, and `undecidable` is how it says so instead of looking like an unlucky search.
"""

from __future__ import annotations

import random

from chimera.ecosystem.spec import AgentSpec, SpecSearchResult, search_spec

#: The scenario suite this gate scores against: eight scenarios, six of them saturated, so exactly
#: two contribute any variance at all (`bench/scenarios/RESULTS.md`).
SCENARIOS = 8


def _search(scores: list[float], **kwargs: object) -> SpecSearchResult:
    """Drive the search with a scripted sequence of scores — the first is the incumbent's."""
    remaining = list(scores)

    def scorer(_spec: AgentSpec) -> float:
        return remaining.pop(0)

    def proposer(spec: AgentSpec, _score: float) -> AgentSpec:
        return AgentSpec(max_steps=spec.max_steps + 1)

    return search_spec(
        AgentSpec(max_steps=5), scorer, proposer, rounds=len(scores) - 1, **kwargs  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- the table


def test_a_tie_no_longer_reads_as_kept() -> None:
    """The one-line half. A candidate that merely did not regress is reported as exactly that."""
    result = _search([0.875, 0.875])
    step = result.history[1]

    assert step.accepted is True, "it did not regress, and that stays true"
    assert step.advanced is False, "but nothing was kept — the incumbent is unchanged"
    assert result.best.max_steps == 5


def test_a_real_improvement_is_both() -> None:
    """The control: a field that were always False would pass the test above and mean nothing."""
    step = _search([0.500, 0.875]).history[1]
    assert (step.accepted, step.advanced) == (True, True)


def test_a_regression_is_neither() -> None:
    step = _search([0.875, 0.500]).history[1]
    assert (step.accepted, step.advanced) == (False, False)


# --------------------------------------------------------------------------- the gate


def test_without_trials_the_old_rule_is_kept_exactly() -> None:
    """`trials=None` is every existing caller, and it has to be byte-identical.

    The statistical gate is opt-in for the same reason the rest of this project's guards are: a
    change that silently rewires a caller who did not ask for it is how a fix becomes an incident.
    """
    result = _search([0.500, 0.625])  # one scenario of difference, no trial count given
    assert result.history[1].advanced is True
    assert result.undecidable == ""


def test_one_scenario_of_difference_no_longer_promotes() -> None:
    """The defect itself. 4/8 against 5/8 is one coin, and it used to be a promotion."""
    result = _search([0.500, 0.625], trials=SCENARIOS)
    assert result.history[1].advanced is False
    assert result.best.max_steps == 5


def test_the_gate_says_when_no_candidate_could_have_won() -> None:
    """"Nothing was promoted" and "nothing could have been promoted" are different sentences.

    Against an incumbent already at 7/8, a **perfect** 8/8 still leaves the difference interval
    touching zero — so a run that promotes nothing is not evidence about the candidates, and the
    table alone cannot tell the two apart.
    """
    result = _search([0.875, 1.000], trials=SCENARIOS)
    assert result.history[1].advanced is False
    assert "no candidate can clear this gate" in result.undecidable
    assert "8/8" in result.undecidable


def test_a_decidable_gate_says_nothing() -> None:
    """The control for the field above: it must not fire whenever the search simply found nothing.

    With enough trials and a low incumbent, a perfect candidate *can* clear the bar, so the run is
    a statement about the candidates again.
    """
    result = _search([0.100, 0.100], trials=400)
    assert result.undecidable == ""


def test_replication_is_what_makes_a_real_gain_detectable() -> None:
    """The point of `--k`, stated as a test rather than as a claim in a docstring.

    The same two pass rates — 0.750 against 0.958 — are undetectable over eight trials and
    significant over forty. Nothing about the candidate changed; the sample did.
    """
    assert _search([0.750, 0.958], trials=SCENARIOS).history[1].advanced is False
    assert _search([0.750, 0.958], trials=SCENARIOS * 5).history[1].advanced is True


# --------------------------------------------------------------------------- the number that named the defect


def test_a_candidate_identical_to_the_incumbent_stops_being_promoted() -> None:
    """Simulated against the suite's own measured noise, because the defect was a rate, not a case.

    Six of eight scenarios are frozen and two flip; with the two independent at p=2/3 the score
    takes three values, and under the shipped `>` rule a NULL candidate — byte-identical to the
    incumbent, which `model_proposer` really does return when the model's JSON does not parse —
    was promoted whenever its coins landed better than the incumbent's. The rate is ~29.6%.

    Under the gate it is zero, and that is the assertion: not "lower", zero. One coin cannot clear a
    Newcombe interval at n=8 no matter how it lands.
    """
    rng = random.Random(20260910)

    def draw() -> float:
        return (6 + sum(rng.random() < 2 / 3 for _ in range(2))) / SCENARIOS

    old = new = 0
    for _ in range(4000):
        incumbent, candidate = draw(), draw()
        old += candidate > incumbent
        new += _search([incumbent, candidate], trials=SCENARIOS).history[1].advanced

    assert 0.24 < old / 4000 < 0.36, "the null promotion rate the old rule had, reproduced"
    assert new == 0, "and no null candidate clears the interval, ever"
