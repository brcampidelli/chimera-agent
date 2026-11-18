"""Four ways a spend ceiling was handed less than was spent (third review of P0-13).

All four are the same failure: a figure that was known, or knowable, replaced by a smaller one rebuilt
from something that cannot see the expensive part.

1. A run's exact cost (priced call by call) lost to ``route_meta.stages``, which describes only its
   LAST step: a 0.1495 run recorded as 0.0825.
2. A ``:batch`` variant (priced at half) read from an index file written before ``pricing.overrides``
   were parsed had no long-prompt tier, so 150,000 tokens priced at the short rate.
3. A cascade's k samples were summarised by the AVERAGE of their prompts: one paid 150k sample beside one
   the local cache served became two of 75k, and the cached tokens turned into a discount.
4. Identity matching leaked into hand-typed ``set_price`` rows: an override registered for the
   OpenRouter route followed the model onto the direct Anthropic route.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core.agent import AgentResult
from chimera.fusion import receipts
from chimera.fusion.receipts import ModelPrice, cost_usd, resolve_price, set_price
from chimera.orchestration.receipts import price_completion
from chimera.providers.gateway import CompletionResult, Message

HI = [Message(role="user", content="hi")]
HAIKU = "openrouter/anthropic/claude-haiku-5.5"


@pytest.fixture
def fresh_table(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: None)


# ------------------------------------------------------------------ 1. the run's exact cost wins


def _run_result(usd: float | None, *, calls: int = 3) -> AgentResult:
    """A run of Haiku 5.5 calls whose route_meta names only the LAST step (160,000 prompt tokens)."""
    return AgentResult(
        answer="", steps=calls, stopped_reason="final", model=HAIKU,
        prompt_tokens=360_000, completion_tokens=3_000, calls=calls, usd=usd,
        route_meta={"stages": [{"model": HAIKU, "prompt_tokens": 160_000, "completion_tokens": 1_000}]},
    )


def test_a_runs_exact_cost_outranks_the_stages_of_its_last_step(fresh_table: None) -> None:
    from chimera.orchestration.budget import SpendBudget

    result = _run_result(0.1495)
    assert price_completion(result).usd == pytest.approx(0.1495), "rebuilt from the last step: 0.0825"
    budget = SpendBudget(0.10, strict=False)
    budget.record_result(result)
    assert budget.spent == pytest.approx(0.1495) and budget.blocked() is not None


def test_a_run_of_unknown_cost_is_unknown_not_its_last_step(fresh_table: None) -> None:
    cost = price_completion(_run_result(None))
    assert cost.unpriced, "neither the last step nor the sums can stand in for an unknown run"


def test_a_fused_single_result_is_still_priced_by_its_stages(fresh_table: None) -> None:
    """The control: a result with no exact cost and no aggregate keeps the stage pricing."""
    fused = CompletionResult(
        content="x", model="fusion",
        route_meta={"stages": [{"model": HAIKU, "prompt_tokens": 160_000, "completion_tokens": 1_000}]},
    )
    assert price_completion(fused).usd == pytest.approx((160_000 * 0.50 + 1_000 * 2.50) / 1e6)


# ------------------------------------------------------------------ 2. a variant with no published tier


def test_a_batch_variant_read_from_an_old_index_file_still_pays_a_long_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.05/0.25 is half the table's 0.10/0.50, so the long tier is the table's 0.50/2.50 halved:
    0.25/1.25. 150,000 in + 1,000 out = 0.03875; the short rate gave 0.00775."""
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: listing.Remembered(
        0.05, 0.25, None, None, None, cache_read_per_m=0.005, cache_write_per_m=0.0625,
    ))
    price = resolve_price(f"{HAIKU}:batch")
    assert price is not None and price.long_prompt is not None and price.long_prompt_over == 100_000
    assert (price.long_prompt.input_per_m, price.long_prompt.output_per_m) == pytest.approx((0.25, 1.25))
    assert cost_usd(price, 150_000, 1_000) == pytest.approx(0.03875)
    assert cost_usd(price, 150_000, 1_000) > 0.00775 * 4, "never the short rate over the threshold"
    assert cost_usd(price, 50_000, 1_000) == pytest.approx(50_000 * 0.05 / 1e6 + 1_000 * 0.25 / 1e6)


def test_a_published_tier_still_wins_over_the_scaled_one(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: listing.Remembered(
        0.05, 0.25, None, None, None, cache_read_per_m=None, cache_write_per_m=None, long_prompt=(100_000, 0.30, 1.40, None, None),
    ))
    price = resolve_price(f"{HAIKU}:batch")
    assert price is not None and price.long_prompt is not None
    assert (price.long_prompt.input_per_m, price.long_prompt.output_per_m) == (0.30, 1.40)


def test_a_model_whose_family_has_no_tier_gets_none_from_the_borrowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: listing.Remembered(
        0.9, 3.0, None, None, None, cache_read_per_m=None, cache_write_per_m=None,
    ))
    price = resolve_price("openrouter/anthropic/claude-sonnet-5.5:batch")
    assert price is not None and price.long_prompt is None


# ------------------------------------------------------------------ 3. a cascade keeps each sample's cost


class _Samples:
    """The first sample is paid and long; the second is the gateway's own response cache answering
    (``prompt_tokens=0`` and the original count under ``cache_read_tokens``, a call nobody billed)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: Any, *, model: str | None = None, **_: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return CompletionResult(
                content="the same answer", model=HAIKU, prompt_tokens=150_000, completion_tokens=1_000,
                finish_reason="stop",
            )
        return CompletionResult(
            content="the same answer", model=HAIKU, prompt_tokens=0, completion_tokens=0,
            cache_read_tokens=150_000, finish_reason="stop",
        )


def test_a_cascades_cost_is_each_sample_priced_alone(fresh_table: None) -> None:
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig

    backend = _Samples()
    cascade = CascadeBackend(backend, _Samples(), CascadeConfig(weak=HAIKU, mid=HAIKU, agreement_k=2))  # type: ignore[arg-type]
    result = cascade.complete(HI, model=None, temperature=0.7)
    long_call = (150_000 * 0.50 + 1_000 * 2.50) / 1e6
    assert long_call == pytest.approx(0.0775)
    assert result.usd == pytest.approx(0.0775), "the cached sample is free and the long one is long"
    assert price_completion(result).usd == pytest.approx(0.0775)
    assert price_completion(result).usd > 0.01, "the averaged tokens priced this at 0.002"


def test_a_cascade_with_an_unpriceable_sample_reports_no_exact_cost(fresh_table: None) -> None:
    from chimera.fusion.cascade import _sum_cost

    ok = CompletionResult(content="x", model=HAIKU, prompt_tokens=1_000, completion_tokens=100)
    unknown = CompletionResult(content="x", model="no/such-model-anywhere", prompt_tokens=1_000, completion_tokens=100)
    assert _sum_cost([ok, unknown]) is None
    assert _sum_cost([ok, ok]) == pytest.approx(2 * (1_000 * 0.10 + 100 * 0.50) / 1e6)


# ------------------------------------------------------------------ 4. a hand-typed override keeps its route


def test_a_set_price_for_the_openrouter_route_does_not_follow_the_model_to_the_direct_one(
    fresh_table: None,
) -> None:
    resolve_price(HAIKU)  # fold the catalogue in first, so the override is the most recent
    set_price("openrouter/anthropic/claude-haiku-5.5", ModelPrice(9.0, 9.0))
    routed = resolve_price("openrouter/anthropic/claude-haiku-5.5")
    direct = resolve_price("anthropic/claude-haiku-5-5")
    assert routed is not None and (routed.input_per_m, routed.output_per_m) == (9.0, 9.0)
    assert direct is not None and (direct.input_per_m, direct.output_per_m) == (0.10, 0.50)


def test_a_hand_typed_substring_still_matches_the_whole_slug_as_before(fresh_table: None) -> None:
    resolve_price(HAIKU)
    set_price("my-lab/claude-ish", ModelPrice(3.0, 4.0))
    price = resolve_price("endpoint/x/my-lab/claude-ish-2")
    assert price is not None and (price.input_per_m, price.output_per_m) == (3.0, 4.0)


def test_the_catalogue_rows_still_match_by_identity(fresh_table: None) -> None:
    assert resolve_price("endpoint/claude-fable-5-2/qwen3") is None
    price = resolve_price("endpoint/my-proxy/claude-haiku-5-5")
    assert price is not None and (price.input_per_m, price.output_per_m) == (0.10, 0.50)


# ------------------------------------------------------------------ 5. a sample with no usage makes the cascade's cost unknown


class _OneWithoutUsage:
    """The first sample reports 150k/1k; the second reports NOTHING (None counters)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: Any, *, model: str | None = None, **_: Any) -> CompletionResult:
        self.calls += 1
        if self.calls == 1:
            return CompletionResult(
                content="the same answer", model=HAIKU, prompt_tokens=150_000, completion_tokens=1_000,
                finish_reason="stop",
            )
        return CompletionResult(
            content="the same answer", model=HAIKU, prompt_tokens=None, completion_tokens=None,
            finish_reason="stop",
        )


def test_a_cascade_with_a_sample_that_reported_no_usage_is_of_unknown_cost(fresh_table: None) -> None:
    """Rebuilt from the averaged totals this read 0.0155 (150k over two calls, both short): a fifth of
    the 0.0775 the one sample that did report already cost."""
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig
    from chimera.orchestration.budget import SpendBudget

    cascade = CascadeBackend(
        _OneWithoutUsage(), _OneWithoutUsage(),  # type: ignore[arg-type]
        CascadeConfig(weak=HAIKU, mid=HAIKU, agreement_k=2),
    )
    result = cascade.complete(HI, model=None, temperature=0.7)
    assert result.usd is None and result.aggregate is True
    cost = price_completion(result)
    assert cost.unpriced, "unknown, never the 0.0155 the averaged totals give"
    budget = SpendBudget(1.0, strict=False)
    budget.record_result(result)
    assert budget.blocked() is not None, "a ceiling must refuse what follows an unknown cost"


# ------------------------------------------------------------------ 6. identity belongs to the row, not to the text


def test_a_set_price_that_reuses_a_catalogue_pattern_text_is_still_a_route_override(
    fresh_table: None,
) -> None:
    resolve_price(HAIKU)  # the catalogue registers "anthropic/claude-haiku-5.5" (identity row) first
    set_price("anthropic/claude-haiku-5.5", ModelPrice(0.01, 0.05))
    bedrock = resolve_price("bedrock/anthropic.claude-haiku-5-5")
    assert bedrock is not None and (bedrock.input_per_m, bedrock.output_per_m) == (0.10, 0.50)
    # On the route whose slug contains the text, the override applies, as a substring always did.
    routed = resolve_price("openrouter/anthropic/claude-haiku-5.5")
    assert routed is not None and (routed.input_per_m, routed.output_per_m) == (0.01, 0.05)
    # The catalogue row is untouched in what it costs and in how it matches.
    assert next(p for pat, p in receipts._PRICES if pat == "claude-haiku-5-5").by_identity is True
