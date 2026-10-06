"""A strict spend cap is asked by the calls a run makes BESIDE its steps, not only by the steps.

``Agent._step`` asks ``SpendBudget.admit`` with the step's worst case when the owner made the ceiling
strict (``CHIMERA_STRICT_SPEND_CAP``). The same run makes two more paid calls from the same budget:
the compaction summariser (``summarise_compaction``) and the tool router (``ToolRouter.pick``). Both
used to call first and charge after, so a step admitted at $0.98 of a $1 strict ceiling could be
followed by a summariser call that carried the run past it.

Now both ask first under a strict ceiling, and a refusal is handled the way each already handles a
failure: the summariser falls back to the structural note, the router to "do not narrow". Off, they
call exactly as before. Free: fake backends, no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from chimera.core.summarise import rule_summariser
from chimera.core.tool_router import ToolRouter
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.orchestration.budget import SpendBudget
from chimera.providers.gateway import CompletionResult

MODEL = "testvendor/side-call-fixed-price"
#: Input free, output $1 per million: a call bounded at 300k tokens costs at most $0.30.
BOUND = 300_000
OLDER = [
    {"role": "user", "content": "use the second file grep found"},
    {"role": "assistant", "content": "ok, the second one"},
]
SCHEMAS = [
    {"type": "function", "function": {"name": "read_file", "description": "read a file"}},
    {"type": "function", "function": {"name": "grep", "description": "search files"}},
]


@pytest.fixture(autouse=True)
def _pinned_price() -> Iterator[None]:
    set_price(MODEL, ModelPrice(input_per_m=0.0, output_per_m=1.0))
    yield


class _Bounded:
    """Answers ``reply`` billed at its whole bound, and says what that bound is, as the gateway does
    with its completion ceiling when the caller sets none."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def planned_call(self, model: str | None = None, max_tokens: int | None = None) -> tuple[str, int]:
        return model or MODEL, max_tokens if max_tokens is not None else BOUND

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.reply, model=MODEL, prompt_tokens=10, completion_tokens=BOUND)


def _budget(spent: float, *, strict: bool) -> SpendBudget:
    budget = SpendBudget(max_usd=1.0, strict=strict)
    budget.charge(spent)
    return budget


# --- the compaction summariser --------------------------------------------------------------------


def test_the_summariser_does_not_call_when_its_worst_case_would_pass_a_strict_ceiling() -> None:
    backend = _Bounded("- keep using the second file")
    budget = _budget(0.9, strict=True)

    summary = rule_summariser(backend, MODEL)(OLDER, spend=budget)

    assert backend.calls == 0
    assert "Standing from that span" not in summary  # the structural note, alone
    assert budget.spent <= budget.max_usd


def test_the_summariser_calls_when_its_worst_case_still_fits() -> None:
    backend = _Bounded("- keep using the second file")
    budget = _budget(0.5, strict=True)

    summary = rule_summariser(backend, MODEL)(OLDER, spend=budget)

    assert backend.calls == 1
    assert "Standing from that span" in summary
    assert budget.spent == pytest.approx(0.8)


def test_off_the_summariser_calls_as_before() -> None:
    backend = _Bounded("- keep using the second file")
    budget = _budget(0.9, strict=False)

    rule_summariser(backend, MODEL)(OLDER, spend=budget)

    assert backend.calls == 1
    assert budget.spent == pytest.approx(1.2)


# --- the tool router --------------------------------------------------------------------------------


def test_the_router_does_not_call_when_its_worst_case_would_pass_a_strict_ceiling() -> None:
    backend = _Bounded("grep")
    router = ToolRouter(backend, MODEL)
    budget = _budget(0.9, strict=True)

    picked = router.pick("find the bug", [], SCHEMAS, spend=budget)

    assert picked is None  # do not narrow: the step runs with every tool
    assert backend.calls == 0
    assert router.stats.fallbacks == 1
    assert budget.spent <= budget.max_usd


def test_the_router_calls_when_its_worst_case_still_fits() -> None:
    backend = _Bounded("grep")
    budget = _budget(0.5, strict=True)

    assert ToolRouter(backend, MODEL).pick("find the bug", [], SCHEMAS, spend=budget) == "grep"
    assert budget.spent == pytest.approx(0.8)


def test_off_the_router_calls_as_before() -> None:
    backend = _Bounded("grep")
    budget = _budget(0.9, strict=False)

    assert ToolRouter(backend, MODEL).pick("find the bug", [], SCHEMAS, spend=budget) == "grep"
    assert budget.spent == pytest.approx(1.2)
