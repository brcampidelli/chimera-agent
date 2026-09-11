"""Self-optimizable agent spec + LLM-guided meta-search (OpenJarvis, 2605.17172).

The agent's behaviour is decomposed into a few **editable primitives** (an
:class:`AgentSpec`), so the whole system can be optimized as one unit instead of tuning
components in isolation. :func:`search_spec` runs the OpenJarvis loop: a proposer (an LLM
diagnosing failures) suggests a *coordinated* edit across primitives, the spec is
re-scored, and the edit is kept only on **non-regression** — the gate that keeps the
search from drifting backwards. Scorer and proposer are injected, so the search is fully
testable; :func:`model_proposer` supplies the LLM-guided one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

_SPEC_FIELDS = {"model", "system_prompt", "max_steps", "fusion_panel", "memory_k"}


@dataclass
class AgentSpec:
    """The editable primitives of the agent, optimizable together."""

    model: str | None = None  # Intelligence
    system_prompt: str = ""  # Agents (reasoning loop)
    max_steps: int = 8  # Engine (runtime budget)
    fusion_panel: list[str] = field(default_factory=list)  # Intelligence diversity
    memory_k: int = 3  # Tools & Memory

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSpec:
        return cls(**{k: v for k, v in data.items() if k in _SPEC_FIELDS})


Scorer = Callable[[AgentSpec], float]
Proposer = Callable[[AgentSpec, float], AgentSpec]


@dataclass
class SearchStep:
    spec: AgentSpec
    score: float
    accepted: bool
    """Did not regress (``>=``). A report about the candidate, not about what the search did."""
    advanced: bool = False
    """The search replaced its incumbent with this candidate.

    Separate from ``accepted`` because the two are not the same and were being printed as if they
    were: the column headed "kept" showed ``accepted``, so a **tie** — the most common outcome
    against a saturated ruler — printed ✓ over an incumbent that had not moved.
    """


@dataclass
class SpecSearchResult:
    best: AgentSpec
    best_score: float
    history: list[SearchStep]
    undecidable: str = ""
    """Why no candidate could have been promoted, or ``""`` when some could.

    Empty is not "the gate works" — it is "the gate is answerable". A gate whose ceiling sits below
    the bar it has to clear will refuse everything forever and look exactly like a search that found
    nothing worth keeping, which is the failure this field exists to make impossible to mistake.
    """


def search_spec(
    initial: AgentSpec,
    scorer: Scorer,
    proposer: Proposer,
    *,
    rounds: int = 3,
    trials: int | None = None,
    z: float = 1.959963984540054,
    error_budget: bool = True,
) -> SpecSearchResult:
    """Propose → evaluate → keep-on-improvement, for ``rounds`` rounds.

    ``error_budget`` spends ``z``'s error rate across the whole search rather than once per round
    (:func:`chimera.eval.anytime.spend_across`). Per round, the gate above promotes a null candidate
    2.5% of the time; over ten rounds that is a one-in-five search that promotes noise, and a
    promotion moves the incumbent, so the next round is measured against noise. Simulated on a
    24-trial ruler (`tests/test_the_search_spends_one_error_budget.py`): without the budget about
    20% of ten-round searches promote a candidate identical to the incumbent; with it, under 3%.
    ``False`` keeps the per-round test, for a caller that budgets elsewhere.

    ``trials`` is the number of independent trials each score is a rate over, and supplying it is
    what turns the comparison into a decision. Without it the gate is ``score > best_score`` on a
    bare fraction, which is what shipped: against the scenario suite that fraction is quantised in
    steps of 1/8 and six of its eight scenarios are saturated, so one scenario of difference — one
    coin — promoted a candidate. A candidate **identical to the incumbent** cleared that gate 29.6%
    of the time.

    With ``trials``, a candidate is promoted only when the Newcombe interval for the difference in
    pass rates excludes zero (:func:`chimera.eval.anytime.proportion_diff_ci`, already the gate in
    ``bench_ab``, ``continuous``, ``paired`` and ``auto_evolve``). That module's own docstring
    measured this exact failure — *"best-of-3 accepted a candidate whose true pass rate was 0.3
    51.8% of the time"* — and this is the one selection gate it had never been pointed at.

    **This does not make the gate work; it makes it say that it does not decide.** On the published
    numbers no achievable candidate clears it, and that is reported in ``undecidable`` rather than
    left to look like an unlucky search.
    """
    from chimera.eval.anytime import proportion_diff_ci, spend_across

    best = initial
    best_score = scorer(initial)
    history = [SearchStep(initial, best_score, True, True)]
    z_round = spend_across(z, max(1, rounds)) if error_budget else z

    def beats(score: float, incumbent: float) -> bool:
        if trials is None or trials <= 0:
            return score > incumbent
        lower, _ = proportion_diff_ci(
            round(score * trials), trials, round(incumbent * trials), trials, z_round
        )
        return lower > 0.0

    for _ in range(max(0, rounds)):
        candidate = proposer(best, best_score)
        score = scorer(candidate)
        accepted = score >= best_score  # non-regression: a report, not a decision
        advanced = beats(score, best_score)
        history.append(SearchStep(candidate, score, accepted, advanced))
        if advanced:
            best, best_score = candidate, score
    return SpecSearchResult(
        best=best,
        best_score=best_score,
        history=history,
        undecidable=_undecidable_reason(best_score, trials, z_round),
    )


def _undecidable_reason(incumbent: float, trials: int | None, z: float) -> str:
    """Whether a **perfect** candidate could clear the gate against ``incumbent``, and why not.

    Asked of the best case rather than of the run that happened: "nothing was promoted" and "nothing
    could have been promoted" are different sentences, and only one of them is about the candidates.
    Mirrors what ``auto_evolve`` does with :func:`chimera.eval.anytime.best_possible_wilson`, in the
    shape a two-proportion gate needs.
    """
    if trials is None or trials <= 0:
        return ""
    from chimera.eval.anytime import proportion_diff_ci

    lower, _ = proportion_diff_ci(trials, trials, round(incumbent * trials), trials, z)
    if lower > 0.0:
        return ""
    return (
        f"no candidate can clear this gate: a perfect {trials}/{trials} against an incumbent at "
        f"{incumbent:.3f} still leaves the difference interval touching zero (lower={lower:+.3f}). "
        f"Raise the trial count or use a ruler the incumbent has not already saturated."
    )


def model_proposer(backend: object, model: str | None = None) -> Proposer:
    """A proposer that asks a model to diagnose the spec and emit a coordinated edit."""

    def propose(spec: AgentSpec, score: float) -> AgentSpec:
        from chimera.providers.gateway import Message

        prompt = (
            f"Current agent spec (JSON):\n{json.dumps(spec.to_dict())}\n\n"
            f"Current benchmark score: {score:.3f}\n"
            "Diagnose likely weaknesses and propose an improved spec. Reply with ONLY a JSON "
            "object using the same keys (model, system_prompt, max_steps, fusion_panel, memory_k)."
        )
        raw = backend.complete(  # type: ignore[attr-defined]
            [Message(role="user", content=prompt)], model=model, temperature=0.3
        ).content
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return spec
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return spec
        return AgentSpec.from_dict({**spec.to_dict(), **data})  # merge the edit onto current

    return propose
