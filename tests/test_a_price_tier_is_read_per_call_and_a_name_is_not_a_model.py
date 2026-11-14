"""A long-prompt tier is read off ONE call, never off a sum; and a slug's price is its model's, not its name's.

Two leftovers of the Claude 5.x catalogue work, both the same kind of defect: a number computed over
the wrong unit.

**Haiku 5.5 bills a request whose prompt is over 100,000 tokens at five times the rate.** That is a
property of ONE request. Anywhere the code adds tokens up first and prices the total afterwards, the
tier is read off the sum: three calls of 60,000 tokens (each short) priced as one call of 180,000
(long) cost 5x what they cost, and a spend cap built on that number refuses a job that never came
near it. The run's own tally prices each call as it arrives and was already right; the places that
only ever see the total were not (the delegation receipt prices ``budget.spent``, a whole worker's
calls summed, and nothing else downstream of an agent run's totals may price them again with a tier).
Where only the aggregate exists and its total passes the threshold, the LONG tier is charged and the
figure is marked an estimate: an upper bound, the safe side for a spend ceiling. (The first version of
this file used the SHORT tier there and pinned it in tests. That UNDER-states the spend, which is the
dangerous direction, and it re-priced an agent run's totals over the exact per-call figure it already
carried: 0.0375 for a run the tally priced at 0.1495.)

**``resolve_price`` matched a table row as a substring of the whole slug.** An endpoint is named by
its owner, so ``endpoint/claude-fable-5-2/qwen3`` contains the text of the Fable and was priced as one.
The row is matched against the model's identity (``catalog.model_identity``), the reading the
capability table already uses.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig, AgentResult
from chimera.fusion import receipts
from chimera.fusion.receipts import cost_usd, resolve_price
from chimera.orchestration.receipts import make_receipt, price_completion, price_delegation
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry

HAIKU = "openrouter/anthropic/claude-haiku-5.5"
FABLE_PRICE = (8.0, 40.0)


@pytest.fixture
def fresh_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """A private price table with the catalogue folded in, and no live index on this machine."""
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_price", lambda _slug: None)
    monkeypatch.setattr(listing, "known_cache_price", lambda _slug: (None, None))


# --------------------------------------------------------------------- a run of three calls


class _Result:
    """The shape ``Agent._step`` reads off a completion."""

    def __init__(self, content: str, model: str, prompt: int, completion: int) -> None:
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
    name = "ping"
    description = "does nothing"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "pong"


class _Backend:
    """Answers on Haiku 5.5 with a scripted prompt size per call; the last call ends the run."""

    def __init__(self, prompts: list[int]) -> None:
        self.prompts = prompts
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        prompt = self.prompts[self.calls]
        self.calls += 1
        last = self.calls == len(self.prompts)
        result = _Result("done" if last else f"step {self.calls}", HAIKU, prompt, 1_000)
        if not last:
            result.tool_calls = [ToolCall(id=f"c{self.calls}", name="ping", arguments={})]
        return result


def _run(prompts: list[int], **config: Any) -> AgentResult:
    registry = ToolRegistry()
    registry.register(_Ping())
    agent = Agent(_Backend(prompts), registry, AgentConfig(model=HAIKU, max_steps=8, **config))
    return agent.run("do the thing")


def test_three_calls_of_60k_cost_three_short_calls_and_do_not_trip_a_cap(fresh_table: None) -> None:
    """Short tier per call: 3 x (60,000 x 0.10 + 1,000 x 0.50) / 1e6 = 0.0195. As ONE call of 180,000
    it would be 0.0975 and a 0.03 ceiling would stop a run that spent two thirds of it."""
    result = _run([60_000, 60_000, 60_000], max_usd=0.03)
    assert result.stopped_reason == "final", result.stopped_reason
    assert result.prompt_tokens == 180_000 and result.calls == 3
    assert result.usd == pytest.approx(0.0195)
    long_on_the_sum = (180_000 * 0.50 + 3_000 * 2.50) / 1e6
    assert result.usd is not None and result.usd < long_on_the_sum / 4


def test_the_runs_own_price_is_per_call_so_a_genuinely_long_call_still_pays_the_long_tier(
    fresh_table: None,
) -> None:
    """The tally keeps the exact figure: 90k is short, 110k and 160k are each over 100k."""
    result = _run([90_000, 110_000, 160_000])
    expected = (
        (90_000 * 0.10 + 1_000 * 0.50)
        + (110_000 * 0.50 + 1_000 * 2.50)
        + (160_000 * 0.50 + 1_000 * 2.50)
    ) / 1e6
    assert result.usd == pytest.approx(expected)


def test_a_run_that_carries_its_exact_cost_is_never_priced_again_from_its_totals(
    fresh_table: None,
) -> None:
    """Calls of 90k, 110k and 160k prompt tokens: the tally prices each at its own tier, 0.1495. The
    token totals alone (360,000) would price at 0.0375 on the short tier (a 75% under-statement) or at
    0.18 on the long one; neither is the figure the run already had, and a ceiling must read that."""
    from chimera.orchestration.budget import SpendBudget

    result = _run([90_000, 110_000, 160_000])
    assert result.aggregate is True and result.usd == pytest.approx(0.1495)
    cost = price_completion(result)
    assert cost.usd == pytest.approx(0.1495) and cost.unpriced is None and cost.estimated is False
    # And the ceiling built on it fires: a 0.10 cap with 0.1495 spent is spent.
    budget = SpendBudget(0.10, strict=False)
    budget.record_result(result)
    assert budget.blocked() is not None
    # A run whose own cost is unknown stays unknown, not zero.
    unknown = _run([90_000, 110_000])
    unknown.usd = None
    assert price_completion(unknown).unpriced

    # A single-call run is that call and keeps its own tier.
    one = _run([160_000])
    assert one.aggregate is False and price_completion(one).usd == pytest.approx(one.usd)


def test_an_aggregate_over_the_threshold_is_charged_at_the_long_tier_and_marked_an_estimate(
    fresh_table: None,
) -> None:
    """Only a total exists, and it is over 100,000: the real figure lies between the two tiers, and a
    spend ceiling must be handed the upper end. Under the threshold every call was shorter still, so
    the figure is exact and not marked."""
    from chimera.fusion.receipts import tier_is_estimate

    price = resolve_price(HAIKU)
    assert price is not None
    assert cost_usd(price, 180_000, 3_000, aggregate=True) == pytest.approx(
        (180_000 * 0.50 + 3_000 * 2.50) / 1e6
    )
    assert tier_is_estimate(price, 180_000, aggregate=True) is True
    assert cost_usd(price, 90_000, 3_000, aggregate=True) == pytest.approx((90_000 * 0.10 + 3_000 * 0.50) / 1e6)
    assert tier_is_estimate(price, 90_000, aggregate=True) is False
    assert tier_is_estimate(price, 180_000, aggregate=False) is False, "one call is exact"
    # k samples of ONE prompt: the average IS the prompt, so the tier follows it either way.
    assert cost_usd(price, 120_000, 2_000, calls=2) == pytest.approx(0.013)
    assert cost_usd(price, 240_000, 0, calls=2) == pytest.approx(240_000 * 0.50 / 1e6)


# --------------------------------------------------------------------- the delegation receipt


def test_a_delegation_receipt_over_a_summed_budget_is_an_upper_bound_and_says_so(
    fresh_table: None,
) -> None:
    """``budget.spent`` is every call a worker made, added together. Over 100,000 the long tier is
    charged to all of it and the receipt is marked an estimate; one call's receipt is exact."""
    from chimera.orchestration.spec import TaskSpec

    spec = TaskSpec(task_id="t", objective="o")
    summed = make_receipt(
        spec, tier="mid", model=HAIKU, prompt_tokens=180_000, completion_tokens=0,
        counterfactual_tokens=500_000, counterfactual_model=HAIKU, aggregate=True,
    )
    assert summed.usd == pytest.approx(180_000 * 0.50 / 1e6) and summed.usd_estimated is True
    assert summed.counterfactual_usd == pytest.approx((400_000 * 0.50 + 100_000 * 2.50) / 1e6)
    one_call = make_receipt(
        spec, tier="top", model=HAIKU, prompt_tokens=180_000, completion_tokens=0,
    )
    assert one_call.usd == pytest.approx(180_000 * 0.50 / 1e6) and one_call.usd_estimated is False
    under = make_receipt(
        spec, tier="mid", model=HAIKU, prompt_tokens=90_000, completion_tokens=0, aggregate=True,
    )
    assert under.usd == pytest.approx(90_000 * 0.10 / 1e6) and under.usd_estimated is False


def test_one_call_priced_through_the_delegation_helper_keeps_its_tier(fresh_table: None) -> None:
    assert price_delegation(HAIKU, 150_000, 1_000) == pytest.approx((150_000 * 0.50 + 1_000 * 2.50) / 1e6)


def test_a_review_prices_each_request_and_adds_the_dollars(
    fresh_table: None, tmp_path: Any
) -> None:
    """The finder's call and the verifier's check are two requests of 60,000 prompt tokens each, so
    two SHORT calls (0.013), not one call of 120,000 (0.065). The verifier's own calls are priced too,
    at the model that answered them."""
    from chimera.providers.gateway import CompletionResult
    from chimera.review import ReviewerChoice, collect, review
    from chimera.review.verifier import CautiousVerifier
    from tests.review_fakes import FakeBackend, finder_json, finding, repo_with_change

    class Big(FakeBackend):
        def complete(self, messages: list[Any], *, model: str | None = None, **kw: Any) -> CompletionResult:
            base = super().complete(messages, model=model, **kw)
            return base.model_copy(update={"prompt_tokens": 60_000, "completion_tokens": 1_000, "model": HAIKU})

    backend = Big(finder_json([finding("calc.py", 11)]))
    reviewer = ReviewerChoice(HAIKU, "openrouter/deepseek/deepseek-v4-flash", "flag")
    report = review(collect(repo_with_change(tmp_path)), backend, reviewer, CautiousVerifier(backend, HAIKU))
    assert [c[0] for c in backend.calls] == ["finder", "verifier"]
    assert report.usage.prompt_tokens == 120_000
    assert report.usage.usd == pytest.approx(0.013)


def test_a_review_with_a_verifier_that_keeps_only_totals_is_never_under_stated(
    fresh_table: None, tmp_path: Any
) -> None:
    """A verifier with no per-call cost: its totals are priced as an aggregate, an upper bound."""
    from chimera.providers.gateway import CompletionResult
    from chimera.review import ReviewerChoice, collect, review
    from tests.review_fakes import FakeBackend, finder_json, repo_with_change

    class Totals:
        name = "totals"

        def check(self, finding: Any, file: Any) -> Any:  # pragma: no cover - no findings
            raise AssertionError

        def usage(self) -> tuple[int, int, int, int]:
            return (2, 0, 150_000, 2_000)

    class Small(FakeBackend):
        def complete(self, messages: list[Any], *, model: str | None = None, **kw: Any) -> CompletionResult:
            base = super().complete(messages, model=model, **kw)
            return base.model_copy(update={"prompt_tokens": 1_000, "completion_tokens": 100, "model": HAIKU})

    reviewer = ReviewerChoice(HAIKU, "openrouter/deepseek/deepseek-v4-flash", "flag")
    report = review(collect(repo_with_change(tmp_path)), Small(finder_json([])), reviewer, Totals())  # type: ignore[arg-type]
    finder = (1_000 * 0.10 + 100 * 0.50) / 1e6
    assert report.usage.usd == pytest.approx(finder + (150_000 * 0.50 + 2_000 * 2.50) / 1e6)


# --------------------------------------------------------------------- a name is not a model


@pytest.mark.parametrize(
    "slug",
    [
        "endpoint/claude-fable-5-2/qwen3",
        "endpoint/claude-fable-5-2/llama-3.3-70b-instruct-q4",
        "openrouter/some-org/claude-fable-5-2-distill-x",
        "endpoint/claude-opus-5-5",  # an endpoint name and no model
    ],
)
def test_an_endpoint_name_or_a_longer_name_is_not_priced_as_the_model_it_contains(
    fresh_table: None, slug: str
) -> None:
    price = resolve_price(slug)
    assert price is None or (price.input_per_m, price.output_per_m) not in {FABLE_PRICE, (4.0, 20.0)}, slug


def test_an_endpoint_that_serves_a_claude_is_priced_as_that_claude(fresh_table: None) -> None:
    price = resolve_price("endpoint/my-proxy/claude-haiku-5-5")
    assert price is not None and (price.input_per_m, price.output_per_m) == (0.10, 0.50)
    assert price.long_prompt is not None


def test_the_other_spellings_still_resolve_after_the_identity_change(fresh_table: None) -> None:
    for spelling, expected in {
        "claude-fable-5-2": FABLE_PRICE,
        "anthropic/claude-fable-5.2": FABLE_PRICE,
        "global.anthropic.claude-fable-5-2": FABLE_PRICE,
        "openrouter/~anthropic/claude-fable-latest": FABLE_PRICE,
        "anthropic/claude-opus-5-5": (4.0, 20.0),
        "openrouter/anthropic/claude-opus-5": (5.0, 25.0),
        "openrouter/anthropic/claude-sonnet-4.5": (3.0, 15.0),
        "claude-haiku-5-5-20261007": (0.10, 0.50),
    }.items():
        price = resolve_price(spelling)
        assert price is not None, spelling
        assert (price.input_per_m, price.output_per_m) == expected, spelling


def test_the_meter_reads_an_exact_cost_instead_of_pricing_the_sums_again(fresh_table: None) -> None:
    """A meter around something that returns a run's totals (and its exact cost) records that cost."""
    from chimera.orchestration.metering import MeteredBackend

    class Summed:
        model = HAIKU
        prompt_tokens = 360_000
        completion_tokens = 3_000
        cache_read_tokens = None
        cache_write_tokens = None
        calls = 3
        aggregate = True
        usd: float | None = 0.1495

    class Inner:
        result = Summed()

        def complete(self, messages: list[Any], **kwargs: Any) -> Summed:
            return self.result

    meter = MeteredBackend(Inner())  # type: ignore[arg-type]
    meter.complete([])
    assert meter.usd == pytest.approx(0.1495)
    # An exact cost that is unknown stays unknown.
    Inner.result = Summed()
    Inner.result.usd = None
    other = MeteredBackend(Inner())  # type: ignore[arg-type]
    other.complete([])
    assert other.usd is None


