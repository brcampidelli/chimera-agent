"""Two fields the orchestration API accepted, documented, published — and never read.

`max_usd` and `synthesize` reached the OpenAPI schema and the generated TypeScript client, so a
caller could set them, get a 200, and receive a run that ignored both. A dollar ceiling that does
not cap and a synthesis switch that does not synthesise are worse than absent ones: absent, a
caller writes their own guard.

`max_usd` was carried as risk #1 in the plan for this route — it spends a top-model decompose, N
mid-model workers and a synthesis, and the `budget` field next to it caps TOKENS PER DELEGATION,
which says nothing about money.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from chimera.orchestration.budget import (
    SpendBudget,
    SpendCappedBackend,
    SpendExceeded,
)
from chimera.providers.gateway import CompletionResult


class _Backend:
    """Charges a known amount per call, by answering as a model the price table knows."""

    def __init__(self, model: str = "openai/gpt-4o-mini") -> None:
        self.model = model
        self.calls = 0
        self.enfeite = "still here"

    def complete(self, _messages: Any, **_kw: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content="ok", model=self.model, prompt_tokens=100_000, completion_tokens=100_000
        )


def _capped(teto: float) -> tuple[SpendCappedBackend, _Backend]:
    inner = _Backend()
    return SpendCappedBackend(inner, SpendBudget(teto)), inner


def test_the_ceiling_stops_the_run() -> None:
    capped, inner = _capped(0.10)

    with pytest.raises(SpendExceeded):
        for _ in range(50):
            capped.complete([{"role": "user", "content": "hi"}])

    assert inner.calls < 50, "every call went through — the wrapper metered without stopping"


def test_it_refuses_before_spending_rather_than_after() -> None:
    """`blocked()` is checked BEFORE the call, so the money is never spent to discover it was over.

    The distinction is invisible in the total and decisive in the bill: a cap checked afterwards
    always overshoots by one call, and one call is the whole ceiling when the ceiling is small.
    """
    capped, inner = _capped(0.10)
    with pytest.raises(SpendExceeded):
        for _ in range(50):
            capped.complete([{"role": "user", "content": "hi"}])

    antes = inner.calls
    with pytest.raises(SpendExceeded):
        capped.complete([{"role": "user", "content": "hi"}])

    assert inner.calls == antes, "it called the model again after the cap had already fired"


def test_a_run_under_its_ceiling_is_not_touched() -> None:
    """The control. A wrapper that raised always would pass every test above."""
    capped, inner = _capped(1_000.0)

    for _ in range(5):
        assert capped.complete([{"role": "user", "content": "hi"}]).content == "ok"
    assert inner.calls == 5


def test_it_is_still_the_backend_it_wraps() -> None:
    """A gateway carries more than `complete`, and callers reach for those attributes by name."""
    capped, _inner = _capped(1.0)

    assert capped.enfeite == "still here"


def test_n_threads_cannot_all_spend_the_last_dollar() -> None:
    """The reason for the lock, and the reason it is held across the call rather than the sum.

    A fan-out calls this from N threads at once. Released between the check and the record, every
    thread reads "under budget" and every thread spends — the cap holds for one caller and fails
    for the only shape this class exists to serve.
    """
    capped, inner = _capped(0.10)
    erros: list[BaseException] = []

    def bater() -> None:
        try:
            for _ in range(20):
                capped.complete([{"role": "user", "content": "hi"}])
        except SpendExceeded:
            pass
        except BaseException as exc:  # noqa: BLE001 -- reported, not swallowed
            erros.append(exc)

    fios = [threading.Thread(target=bater) for _ in range(8)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(30)

    assert not erros, f"threads raised something other than the cap: {erros}"
    # One caller may be inside a call when the cap fires, so the bound is "not many times over",
    # not "exactly at". Without the lock this runs to 8 x 20.
    assert inner.calls < 20, f"{inner.calls} calls got through a cap that should stop ~8"


def test_the_dollar_cap_is_reported_as_its_own_reason() -> None:
    """Two ceilings, two things for the person watching to raise.

    `SpendExceeded` subclasses `BudgetExceeded` so every handler already written keeps working —
    which is exactly why it needs its own type: caught as the parent everywhere, a spend cut would
    reach the screen as "ran out of delegation budget", pointing at the wrong number.
    """
    from chimera.orchestration.budget import BudgetExceeded
    from chimera.orchestration.hierarchy import _CUT_OFF_REASONS

    assert issubclass(SpendExceeded, BudgetExceeded)
    assert "spend" in _CUT_OFF_REASONS, "a spend cut would be verified and synthesised as a finding"
    assert "budget" in _CUT_OFF_REASONS


def test_max_usd_refuses_zero_at_the_edge() -> None:
    """`SpendBudget` rejects it, so accepting it in the request turns a 422 into a 500."""
    from chimera.api.orchestration_api import HierarchyRunIn

    with pytest.raises(ValueError):
        HierarchyRunIn(task="x", max_usd=0)
    with pytest.raises(ValueError):
        HierarchyRunIn(task="x", max_usd=-1)
    assert HierarchyRunIn(task="x", max_usd=None).max_usd is None
