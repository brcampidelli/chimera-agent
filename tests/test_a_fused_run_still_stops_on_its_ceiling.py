"""A spend ceiling or a missing key still stops a fused run (study 30, S30-02 follow-up).

S30-02 made a judge or synthesiser failure fall back to a panel answer. The catch was
``except Exception``, and two failures are not faults: ``SpendExceeded`` (the person set a ceiling
and the run reached it; ``orchestration_api`` catches that exact type to say "spend ceiling
reached") and ``MissingCredentialsError`` (no key, every call, every time). Swallowed, a ceiling hit
at the judge became a reported success, and a ceiling hit before the panel became
:class:`FusionFailed` — a ``RuntimeError`` the ``except SpendExceeded`` no longer matched.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.fusion import FusionConfig, FusionEngine
from chimera.orchestration.budget import (
    BudgetExceeded,
    SpendBudget,
    SpendCappedBackend,
    SpendExceeded,
)
from chimera.providers import CompletionResult
from chimera.providers.gateway import MissingCredentialsError

FULL = FusionConfig(panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth")
SELECTIVE = FusionConfig(
    panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth", mode="selective", probe_k=2
)
ASK = [{"role": "user", "content": "q"}]


class Raising:
    """A panel that answers, and named stages that raise a given exception instead."""

    def __init__(self, raise_on: set[str], exc: Exception, answers: dict[str, str] | None = None) -> None:
        self.raise_on = raise_on
        self.exc = exc
        self.answers = answers or {"m1": "alpha one", "m2": "beta two", "m3": "gamma three"}

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        if model in self.raise_on:
            raise self.exc
        content = self.answers.get(str(model), model.upper() if model else "")
        return CompletionResult(content=content, model=str(model), finish_reason="stop")


def test_a_spend_ceiling_reached_at_the_judge_stops_the_run() -> None:
    with pytest.raises(SpendExceeded, match="ceiling"):
        FusionEngine(Raising({"judge"}, SpendExceeded("spend ceiling US$1 reached")), FULL).run(ASK)


def test_a_spend_ceiling_reached_at_the_synthesiser_stops_the_run() -> None:
    with pytest.raises(SpendExceeded):
        FusionEngine(Raising({"synth"}, SpendExceeded("spend ceiling reached")), FULL).run(ASK)


def test_a_spend_ceiling_reached_at_the_agreed_synthesis_stops_the_run() -> None:
    backend = Raising(
        {"synth"}, SpendExceeded("spend ceiling reached"), {"m1": "the answer is 42", "m2": "the answer is 42"}
    )
    with pytest.raises(SpendExceeded):
        FusionEngine(backend, SELECTIVE).run(ASK)


def test_a_spend_ceiling_reached_before_the_panel_keeps_its_type() -> None:
    backend = Raising({"m1", "m2", "m3", "judge", "synth"}, SpendExceeded("spend ceiling reached"))
    with pytest.raises(SpendExceeded):
        FusionEngine(backend, FULL).run(ASK)
    with pytest.raises(SpendExceeded):
        FusionEngine(backend, SELECTIVE).run(ASK)


def test_a_real_spend_capped_backend_already_over_its_ceiling_raises_spend_exceeded() -> None:
    budget = SpendBudget(1.0)
    budget.charge(2.0)  # already past the ceiling: the wrapper refuses before any call
    capped = SpendCappedBackend(Raising(set(), RuntimeError("unused")), budget)
    with pytest.raises(SpendExceeded):
        FusionEngine(capped, FULL).complete(ASK)


def test_a_token_budget_stop_at_the_judge_is_not_a_fallback_either() -> None:
    with pytest.raises(BudgetExceeded):
        FusionEngine(Raising({"judge"}, BudgetExceeded("token budget")), FULL).run(ASK)


def test_a_missing_key_at_the_judge_is_reported_not_papered_over() -> None:
    with pytest.raises(MissingCredentialsError):
        FusionEngine(Raising({"judge"}, MissingCredentialsError("no key for judge")), FULL).run(ASK)


def test_a_panel_with_no_key_anywhere_raises_the_credential_error() -> None:
    backend = Raising({"m1", "m2", "m3"}, MissingCredentialsError("no provider key configured"))
    with pytest.raises(MissingCredentialsError):
        FusionEngine(backend, FULL).run(ASK)


def test_an_ordinary_judge_failure_still_falls_back() -> None:
    trace = FusionEngine(Raising({"judge"}, RuntimeError("judge 429")), FULL).run(ASK)
    assert trace.aggregation == "fallback"
    assert trace.final == "alpha one"
