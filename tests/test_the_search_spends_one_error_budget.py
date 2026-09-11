"""A search that makes ten decisions at 2.5% each is not a 2.5% search.

`search_spec` promotes a candidate when the Newcombe interval on the difference clears zero at
`z` — a one-sided 2.5% per round. Over `rounds` rounds that budget is spent `rounds` times, and a
promotion moves the incumbent, so every later round is measured against whatever noise won. PACE
(arXiv 2606.08106) and GRASP (2605.29668), read in the 2026-09-11 sweep (item C4), budget the
whole loop; study 10 had found the same multiplicity in `auto_evolve` and left it. `spend_across`
is the Bonferroni split — it holds under any dependence between rounds, which a search's rounds
have — and `search_spec` uses it by default.

Simulated, not argued: null candidates (the same true pass rate as the incumbent) on a 24-trial
ruler, the shape `evolve tune --k 3` gives eight scenarios. The rates below are the assertion.
"""

from __future__ import annotations

import random
from statistics import NormalDist

from chimera.ecosystem.spec import AgentSpec, search_spec
from chimera.eval.anytime import spend_across

TRIALS = 24
ROUNDS = 10


def test_spend_across_splits_the_one_sided_error_rate() -> None:
    z = 1.959963984540054
    assert spend_across(z, 1) == z
    z10 = spend_across(z, 10)
    assert z10 > z
    # 2.5% / 10 per round: the one-sided tail at the new z is a tenth of the old one.
    assert abs((1 - NormalDist().cdf(z10)) - 0.025 / 10) < 1e-9


def _null_searches(*, error_budget: bool, seed: int, searches: int = 600, p: float = 0.6) -> int:
    """How many of `searches` ten-round searches promote at least one null candidate."""
    rng = random.Random(seed)

    def draw() -> float:
        return sum(rng.random() < p for _ in range(TRIALS)) / TRIALS

    promoted = 0
    for _ in range(searches):
        result = search_spec(
            AgentSpec(), scorer=lambda spec: draw(), proposer=lambda spec, score: AgentSpec(),
            rounds=ROUNDS, trials=TRIALS, error_budget=error_budget,
        )
        promoted += any(step.advanced for step in result.history[1:])
    return promoted


def test_without_the_budget_one_search_in_five_promotes_noise_and_with_it_almost_none() -> None:
    without = _null_searches(error_budget=False, seed=20260911)
    with_budget = _null_searches(error_budget=True, seed=20260911)
    # 1 − 0.975^10 ≈ 22%; the discrete ruler lands near it. The budget brings it under 2.5%.
    assert 0.12 < without / 600 < 0.32, f"per-round gate promoted noise in {without}/600 searches"
    assert with_budget / 600 <= 0.04, f"budgeted gate promoted noise in {with_budget}/600 searches"
    assert with_budget < without / 3


def test_a_real_gain_still_clears_the_budgeted_gate() -> None:
    """The price of the budget is a wider interval, not a gate nothing can pass."""
    it = iter([12 / TRIALS, 24 / TRIALS])
    result = search_spec(
        AgentSpec(), scorer=lambda spec: next(it), proposer=lambda spec, score: AgentSpec(),
        rounds=1, trials=TRIALS,
    )
    assert result.history[1].advanced


def test_the_undecidable_note_reads_the_budgeted_z() -> None:
    """`undecidable` asks whether a perfect candidate could clear the gate — the gate as budgeted."""
    result = search_spec(
        AgentSpec(), scorer=lambda spec: 18 / TRIALS, proposer=lambda spec, score: AgentSpec(),
        rounds=10, trials=TRIALS,
    )
    assert "no candidate can clear this gate" in result.undecidable
    # The same incumbent with one round to decide IS decidable: the note is about the budgeted z.
    single = search_spec(
        AgentSpec(), scorer=lambda spec: 18 / TRIALS, proposer=lambda spec, score: AgentSpec(),
        rounds=1, trials=TRIALS,
    )
    assert single.undecidable == ""
