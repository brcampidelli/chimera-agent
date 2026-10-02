"""A missing price, and money spent, are warnings until the person typed a ceiling.

The owner decided on 2026-09-27 that these stop being reasons for the agent to stop. What stays a
stop is a ceiling the person set: it is theirs, and a ceiling that skips what it cannot price shows
green while the real spend climbs. So the rule has two halves and this file holds both, because
the second half is the one a careless version of the first would delete.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.orchestration.budget import SpendBudget
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry

PRICED = "openrouter/deepseek/deepseek-chat"
UNPRICED = "brand-new-model-with-no-price"


@pytest.fixture(autouse=True)
def _pinned_price() -> Iterator[None]:
    # 1,000 in + 1,000 out at $100 per million each = $0.20 a call, so a warning at $0.30 is
    # crossed on the second call and a ceiling of $0.25 is crossed on it too.
    set_price(PRICED, ModelPrice(input_per_m=100.0, output_per_m=100.0))
    yield


class _Result:
    def __init__(self, model: str, n: int) -> None:
        self.content = f"answer {n}"
        self.model = model
        self.prompt_tokens = 1000
        self.completion_tokens = 1000
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls = [ToolCall(id=f"c{n}", name="ping", arguments={})]
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Ping(Tool):
    name = "ping"
    description = "does nothing"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "pong"


class _Backend:
    """Calls `ping` on every step, so only a stop can end the run before max_steps."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        return _Result(self.model, self.calls)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Ping())
    return registry


def _run(model: str, **config: Any) -> tuple[Any, Any, list[tuple[str, str, dict[str, Any]]]]:
    heard: list[tuple[str, str, dict[str, Any]]] = []
    backend = _Backend(model)
    agent = Agent(backend, _registry(), AgentConfig(model=model, detect_tool_loops=False, **config))
    result = agent.run("go", on_notice=lambda c, t, d: heard.append((c, t, d)))
    return result, backend, heard


# --- the budget object ------------------------------------------------------------------------


def test_without_a_ceiling_an_unpriced_call_is_said_once_and_does_not_block() -> None:
    budget = SpendBudget()
    budget.record(UNPRICED, 1000, 1000)

    assert budget.blocked() is None
    first = budget.take_notices()
    assert [n[0] for n in first] == ["price_unknown"]
    assert UNPRICED in first[0][1]
    assert budget.take_notices() == []  # said once, not on every step


def test_with_a_ceiling_an_unpriced_call_still_blocks_and_is_not_a_notice() -> None:
    budget = SpendBudget(max_usd=5.0)
    budget.record(UNPRICED, 1000, 1000)

    assert budget.blocked() is not None and UNPRICED in budget.blocked()  # type: ignore[operator]
    assert budget.take_notices() == []


def test_the_spend_warning_comes_once_when_the_threshold_is_crossed() -> None:
    budget = SpendBudget(warn_usd=0.30)
    budget.charge(0.20)
    assert budget.take_notices() == []
    budget.charge(0.20)
    warned = budget.take_notices()
    assert [n[0] for n in warned] == ["spend_warn"]
    assert warned[0][2]["usd"] == pytest.approx(0.40)
    budget.charge(1.0)
    assert budget.take_notices() == []


def test_no_ceiling_means_the_cap_arithmetic_never_fires() -> None:
    budget = SpendBudget(warn_usd=0.01)
    budget.charge(1_000_000.0)

    assert budget.blocked() is None
    assert budget.capped is False


def test_a_budget_still_must_be_positive() -> None:
    with pytest.raises(ValueError):
        SpendBudget(max_usd=0)
    with pytest.raises(ValueError):
        SpendBudget(warn_usd=-1)


# --- through the loop -------------------------------------------------------------------------


def test_a_run_on_an_unpriced_model_without_a_ceiling_finishes_its_steps_and_warns() -> None:
    result, backend, heard = _run(UNPRICED, max_steps=4, warn_usd=1.0)

    assert result.stopped_reason == "max_steps"  # it was not stopped for the missing price
    assert backend.calls >= 4
    assert [c for c, _t, _d in heard].count("price_unknown") == 1


def test_a_run_on_an_unpriced_model_with_a_ceiling_still_stops_at_the_first_call() -> None:
    result, backend, heard = _run(UNPRICED, max_steps=20, max_usd=100.0)

    assert result.stopped_reason == "spend"
    assert backend.calls == 1
    assert "price_unknown" not in [c for c, _t, _d in heard]


def test_a_run_that_passes_the_warning_amount_says_so_and_keeps_going() -> None:
    result, backend, heard = _run(PRICED, max_steps=5, warn_usd=0.30)

    assert result.stopped_reason == "max_steps"  # $1.00 spent, and nothing stopped it
    assert backend.calls >= 5
    assert [c for c, _t, _d in heard].count("spend_warn") == 1


def test_a_ceiling_the_person_set_still_stops_the_run() -> None:
    result, _backend, _heard = _run(PRICED, max_steps=20, max_usd=0.25, warn_usd=0.10)

    assert result.stopped_reason == "spend"
