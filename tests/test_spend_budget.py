"""The dollar ceiling, and the one decision that makes it honest.

A cap that skips what it cannot price is worse than no cap: it shows green while the real spend
climbs, and it climbs fastest on exactly the models nobody bothered to price. So an unpriced call
stops the run — the owner's decision, recorded here as behaviour rather than as a comment.

The counterpart matters just as much: a LOCAL model is not unpriced, it is free. Getting that wrong
would make a spend cap refuse the one configuration that cannot overspend.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig
from chimera.orchestration.budget import SpendBudget
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry


class _Result:
    """The shape `Agent._step` reads off a completion."""

    def __init__(self, content: str, model: str, prompt: int = 1000, completion: int = 1000) -> None:
        self.content = content
        self.model = model
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls: list[Any] = []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Ping(Tool):
    """A tool that does nothing, so the loop has a reason to keep going.

    Without it the fake backend answers prose on step 1 and the run ends `final` before any budget
    can bite — which would make every assertion below pass for the wrong reason.
    """

    name = "ping"
    description = "does nothing"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "pong"


class _Backend:
    """Calls `ping` forever, on a model of the caller's choosing — so only a cap can end the run."""

    def __init__(self, model: str = "openrouter/deepseek/deepseek-chat") -> None:
        self.model = model
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        result = _Result(f"answer {self.calls}", self.model)
        result.tool_calls = [ToolCall(id=f"c{self.calls}", name="ping", arguments={})]
        return result


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Ping())
    return registry


# --- the budget object itself -----------------------------------------------------------------


def test_an_unpriced_model_stops_the_budget_rather_than_being_skipped() -> None:
    budget = SpendBudget(max_usd=10.0)

    budget.record("some-model-nobody-priced", 1000, 1000)

    assert budget.unpriced_model == "some-model-nobody-priced"
    assert "unknown" in (budget.blocked() or "")


def test_the_unknown_is_sticky() -> None:
    """One unpriced call poisons the total, and a later priced call must not clear it.

    Otherwise the run resumes with a spend figure that is confidently too low — too low in exactly
    the direction that flatters whichever configuration used the unpriced model.
    """
    budget = SpendBudget(max_usd=10.0)
    budget.record("some-model-nobody-priced", 1000, 1000)

    budget.record("openrouter/deepseek/deepseek-chat", 1000, 1000)

    assert budget.blocked() is not None


def test_a_local_model_is_free_not_unknown() -> None:
    # An Ollama run bills nothing to any provider. Treating it as unknown would make the cap refuse
    # the single configuration that cannot possibly overspend.
    budget = SpendBudget(max_usd=1.0)

    budget.record("ollama/llama3", 100_000, 100_000)

    assert budget.blocked() is None
    assert budget.spent == 0.0


def test_a_free_tier_slug_is_also_zero() -> None:
    budget = SpendBudget(max_usd=1.0)

    budget.record("openrouter/meta-llama/llama-3.3-70b-instruct:free", 100_000, 100_000)

    assert budget.blocked() is None


def test_it_blocks_once_the_cap_is_reached_and_says_the_numbers() -> None:
    budget = SpendBudget(max_usd=0.001)

    budget.record("openrouter/deepseek/deepseek-chat", 1_000_000, 1_000_000)  # $0.42

    reason = budget.blocked() or ""
    assert "0.4200" in reason and "0.0010" in reason


def test_a_budget_must_be_positive() -> None:
    with pytest.raises(ValueError):
        SpendBudget(max_usd=0)


# --- wired into the loop ----------------------------------------------------------------------


def test_the_loop_stops_on_the_cap_and_says_so() -> None:
    """`stopped_reason` is its own value, distinct from `max_steps`.

    A budget stop reported as `max_steps` would send whoever reads the receipt looking for a loop
    that never happened, and would hide the one fact that needs acting on.
    """
    backend = _Backend()
    agent = Agent(
        backend,
        _registry(),
        AgentConfig(model="openrouter/deepseek/deepseek-chat", max_steps=20, max_usd=0.0005),
    )

    result = agent.run("do something long")

    assert result.stopped_reason == "budget"
    assert "spend cap reached" in result.answer
    # The first call is allowed: the cap is checked before each call, and before the first one
    # nothing has been spent. Refusing at zero spend would make any cap unusable.
    assert backend.calls >= 1
    assert backend.calls < 20, "the loop kept paying after the cap"


def test_the_loop_stops_when_it_cannot_price_the_model() -> None:
    # The owner's rule, end to end: unknown price is a stop, not a skip.
    backend = _Backend(model="brand-new-model-with-no-price")
    agent = Agent(
        backend,
        _registry(),
        AgentConfig(model="brand-new-model-with-no-price", max_steps=20, max_usd=100.0),
    )

    result = agent.run("do something")

    assert result.stopped_reason == "budget"
    assert "brand-new-model-with-no-price" in result.answer
    assert backend.calls == 1, "it paid for a second call it could not price"


def test_no_cap_means_no_new_way_to_stop() -> None:
    """The default has to be inert. This runs on a production machine, and a limiter that changes
    behaviour for people who did not ask for one is a regression shipped as a feature."""
    backend = _Backend(model="brand-new-model-with-no-price")
    agent = Agent(backend, _registry(), AgentConfig(model="brand-new-model-with-no-price", max_steps=3))

    result = agent.run("do something")

    assert result.stopped_reason != "budget"
    assert backend.calls >= 1


def test_two_runs_of_one_agent_get_separate_budgets() -> None:
    # A scheduler dispatches many jobs through one Agent. A cap carried across runs would refuse the
    # second task because the first spent its allowance — a limiter that behaves differently
    # depending on what ran before it is one nobody can reason about.
    backend = _Backend()
    agent = Agent(
        backend,
        _registry(),
        AgentConfig(model="openrouter/deepseek/deepseek-chat", max_steps=4, max_usd=0.0005),
    )

    first = agent.run("task one")
    second = agent.run("task two")

    assert first.stopped_reason == "budget"
    assert second.stopped_reason == "budget", "the second run started already over budget"


def test_the_partial_answer_survives_the_stop() -> None:
    """The transcript up to the stop is work already paid for. Discarding it would spend the whole
    budget and hand back nothing."""
    backend = _Backend()
    agent = Agent(
        backend,
        _registry(),
        AgentConfig(model="openrouter/deepseek/deepseek-chat", max_steps=20, max_usd=0.0005),
    )

    result = agent.run("do something long")

    assert result.transcript
    assert result.prompt_tokens > 0


# --- the fused turn, which the ceiling could not see at all ---------------------------------------


def _fused(*stages: tuple[str, str, int, int], total_prompt: int, total_completion: int) -> _Result:
    """A completion shaped like the one `FusionEngine.complete` returns.

    The label is the whole problem: it answers as `model="fusion"`, and `"fusion"` is not a model —
    no price table resolves it, and everything downstream priced it as if it were one.
    """
    result = _Result("fused answer", "fusion", total_prompt, total_completion)
    result.route_meta = {
        "stages": [
            {"stage": s, "model": m, "prompt_tokens": p, "completion_tokens": c}
            for s, m, p, c in stages
        ]
    }
    return result


def test_a_fused_turn_is_charged_by_its_stages_not_by_its_label() -> None:
    """The regression that mattered: the app's most expensive turn cost the ceiling $0.00.

    A fused turn makes N panel calls plus a judge plus a synthesizer, then returns ONE result
    labelled `fusion`. `resolve_price("fusion")` is None — verified: no exact entry, no substring
    hit, and it is not a local model — so the whole turn was skipped and `_spent` stayed at zero
    while the ceiling sat in the same composer row as the button that started it.
    """
    budget = SpendBudget(max_usd=10.0)
    priced = "openrouter/deepseek/deepseek-chat"

    budget.record_result(
        _fused(
            ("panel", priced, 1000, 1000),
            ("panel", priced, 1000, 1000),
            ("judge", priced, 500, 200),
            ("synth", priced, 800, 400),
            total_prompt=3300,
            total_completion=2600,
        )
    )

    assert budget.unpriced_model is None, "every stage here has a list price"
    assert budget.spent > 0, "a fused turn costs money and the ceiling must see it"
    # And it equals the sum of the stages at their own rates — not the total tokens at one rate,
    # which is the other wrong number the same turn used to produce.
    from chimera.orchestration.receipts import price_delegation

    expected = sum(
        price_delegation(priced, p, c) or 0.0
        for p, c in ((1000, 1000), (1000, 1000), (500, 200), (800, 400))
    )
    assert budget.spent == pytest.approx(expected)


def test_a_fused_turn_with_one_unpriced_stage_reports_a_floor_and_names_the_gap() -> None:
    budget = SpendBudget(max_usd=10.0)

    budget.record_result(
        _fused(
            ("panel", "openrouter/deepseek/deepseek-chat", 1000, 1000),
            ("panel", "some-model-nobody-priced", 1000, 1000),
            ("synth", "openrouter/deepseek/deepseek-chat", 800, 400),
            total_prompt=2800,
            total_completion=2400,
        )
    )

    # Named, so the fix is actionable — "the price of fusion is unknown" pointed at a model the
    # user never chose and could not register.
    assert budget.unpriced_model == "some-model-nobody-priced"
    assert "unknown" in (budget.blocked() or "")
    # The priced stages really were spent. Reporting them as a floor beats reporting zero.
    assert budget.spent > 0


def test_a_priced_model_that_reported_no_usage_costs_an_unknown_amount_not_zero() -> None:
    """The rule `price_stage` already stated, restated on the path the ceiling consults.

    Otherwise a provider that omits usage makes every call free, and the cap never fires — the same
    failure as the label, arriving by a different door.
    """
    budget = SpendBudget(max_usd=10.0)
    result = _Result("hi", "openrouter/deepseek/deepseek-chat")
    result.prompt_tokens = None
    result.completion_tokens = None

    budget.record_result(result)

    assert budget.unpriced_model == "openrouter/deepseek/deepseek-chat"
    assert budget.spent == 0.0


def test_a_plain_call_still_charges_the_model_that_answered() -> None:
    budget = SpendBudget(max_usd=10.0)

    budget.record_result(_Result("hi", "openrouter/deepseek/deepseek-chat", 1000, 1000))

    assert budget.unpriced_model is None
    assert budget.spent > 0
