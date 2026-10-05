"""A receipt prices cache reads and cache writes at their own rates.

Every pricer here billed ``prompt_tokens`` at the full input rate. On every route LiteLLM serves,
``prompt_tokens`` already CONTAINS the cache tokens — Anthropic's adapter adds
``cache_read_input_tokens`` and ``cache_creation_input_tokens`` into it, and the OpenAI shape reports
``cached_tokens`` as a subset of it. So nothing was omitted or double-counted; the cache share was
priced at the wrong rate, and the error ran both ways: a cache READ billed at ~0.1x was charged at
1x (a cache-heavy session read up to ten times dearer than it was), and a cache WRITE billed at
1.25x was charged at 1x (a session that builds its cache read cheaper than it was).

The write COUNT the gateway records is covered by `test_the_openrouter_route_reports_its_cache_writes`.

An unknown cache rate is never invented. The cache share then falls back to the input rate — the
number every receipt used before, and a floor for writes, which no provider bills below input.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.fusion.receipts import ModelPrice, resolve_price, set_price
from chimera.orchestration.receipts import price_completion, price_delegation
from chimera.providers import CompletionResult

#: A slug no family pattern matches, so the price under test is the one pinned here.
MODEL = "openrouter/cache-probe/model-qq"
#: 3.00 in, 15.00 out, reads at 0.30, writes at 3.75 — Anthropic's published shape (0.1x / 1.25x).
CACHED = ModelPrice(3.0, 15.0, cache_read_per_m=0.30, cache_write_per_m=3.75)


def test_cache_reads_are_priced_at_the_read_rate() -> None:
    set_price(MODEL, CACHED)

    # 1,000 prompt tokens of which 900 were read from the cache: 100 x 3.00 + 900 x 0.30.
    usd = price_delegation(MODEL, 1_000, 0, cache_read_tokens=900)

    assert usd == pytest.approx((100 * 3.0 + 900 * 0.30) / 1_000_000)
    assert usd is not None and usd < 1_000 * 3.0 / 1_000_000, "a read cost as much as a fresh token"


def test_cache_writes_are_priced_at_the_write_rate() -> None:
    """The direction the old receipt hid: a write costs MORE than a fresh token."""
    set_price(MODEL, CACHED)

    usd = price_delegation(MODEL, 1_000, 0, cache_write_tokens=1_000)

    assert usd == pytest.approx(1_000 * 3.75 / 1_000_000)
    assert usd is not None and usd > 1_000 * 3.0 / 1_000_000, "a write cost no more than a fresh token"


def test_reads_writes_fresh_and_output_add_up() -> None:
    set_price(MODEL, CACHED)

    usd = price_delegation(MODEL, 1_000, 200, cache_read_tokens=800, cache_write_tokens=100)

    expected = (100 * 3.0 + 800 * 0.30 + 100 * 3.75 + 200 * 15.0) / 1_000_000
    assert usd == pytest.approx(expected)


def test_an_unknown_cache_rate_is_not_invented() -> None:
    """No read or write rate on record: the cache share is priced as before, at the input rate. That
    over-states a read and is a floor for a write, and it is a number somebody published — not a
    multiplier this code made up for a provider that never said one."""
    set_price(MODEL, ModelPrice(3.0, 15.0))

    usd = price_delegation(MODEL, 1_000, 0, cache_read_tokens=800, cache_write_tokens=100)

    assert usd == pytest.approx(1_000 * 3.0 / 1_000_000)
    price = resolve_price(MODEL)
    assert price is not None and price.cache_read_per_m is None and price.cache_write_per_m is None


def test_a_response_cache_hit_still_costs_nothing() -> None:
    """The gateway's own response cache answers with ``prompt_tokens=0`` and the original count under
    ``cache_read_tokens``, so that a tally does not bill a call that was never made. Cache tokens
    are a part of the prompt; more of them than the prompt holds were not billed by anybody."""
    set_price(MODEL, CACHED)

    hit = CompletionResult(
        content="x", model=MODEL, prompt_tokens=0, completion_tokens=0, cache_read_tokens=5_000
    )

    assert price_delegation(MODEL, 0, 0, cache_read_tokens=5_000) == 0.0
    assert price_completion(hit).usd == 0.0


def test_a_completed_call_is_priced_with_its_cache_tokens() -> None:
    """`price_completion` is what the spend cap, the agent's tally and the hosted decisions read."""
    set_price(MODEL, CACHED)
    result = CompletionResult(
        content="x", model=MODEL, prompt_tokens=10_000, completion_tokens=0,
        cache_read_tokens=9_000, cache_write_tokens=0,
    )

    cost = price_completion(result)

    assert cost.unpriced is None
    assert cost.usd == pytest.approx((1_000 * 3.0 + 9_000 * 0.30) / 1_000_000)


def test_a_delegation_receipt_prices_its_cache_tokens() -> None:
    from chimera.orchestration.receipts import make_receipt
    from chimera.orchestration.spec import TaskSpec

    set_price(MODEL, CACHED)
    spec = TaskSpec(task_id="t1", objective="g")

    receipt = make_receipt(
        spec, tier="top", model=MODEL, prompt_tokens=1_000, completion_tokens=0,
        cache_read_tokens=900,
    )

    assert receipt.usd == pytest.approx((100 * 3.0 + 900 * 0.30) / 1_000_000)


def test_the_meter_prices_its_cache_tokens() -> None:
    from chimera.orchestration.metering import MeteredBackend

    set_price(MODEL, CACHED)

    class _Cached:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            return CompletionResult(
                content="ok", model=MODEL, prompt_tokens=1_000, completion_tokens=0,
                cache_read_tokens=900,
            )

    meter = MeteredBackend(_Cached())
    meter.complete([{"role": "user", "content": "x"}])

    assert meter.usd == pytest.approx((100 * 3.0 + 900 * 0.30) / 1_000_000)


def test_the_family_table_carries_anthropics_cache_rates() -> None:
    """Anthropic publishes reads at 0.1x and five-minute writes at 1.25x of input, and the cache
    breakpoints this app sets (`prompt_cache`) are the default five-minute kind."""
    sonnet = resolve_price("anthropic/claude-sonnet-4-6")

    assert sonnet is not None
    assert (sonnet.cache_read_per_m, sonnet.cache_write_per_m) == (
        pytest.approx(0.30), pytest.approx(3.75),
    )


def _index_entry(model_id: str, pricing: dict[str, str]) -> dict[str, Any]:
    """One OpenRouter index entry, in the shape their endpoint returns (per-token decimal strings)."""
    return {"id": model_id, "name": f"Vendor: {model_id}", "context_length": 128_000, "pricing": pricing}


def test_the_index_cache_rates_reach_the_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """OpenRouter publishes ``input_cache_read`` and ``input_cache_write`` beside the input price for
    every model that caches. They were never read, so even a model whose read rate the provider had
    told us priced its reads at the full rate."""
    from chimera.providers import listing

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    option = listing._openrouter_option(
        _index_entry(
            "cache-probe/indexed",
            {
                "prompt": "0.000003", "completion": "0.000015",
                "input_cache_read": "0.0000003", "input_cache_write": "0.00000375",
            },
        )
    )
    assert option is not None
    assert (option.cache_read_per_m, option.cache_write_per_m) == (0.3, 3.75)

    listing.remember_models([option], home=tmp_path)

    assert listing.known_cache_price("openrouter/cache-probe/indexed") == (0.3, 3.75)
    price = resolve_price("openrouter/cache-probe/indexed")
    assert price is not None
    assert (price.cache_read_per_m, price.cache_write_per_m) == (0.3, 3.75)


def test_an_index_row_without_cache_rates_leaves_them_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from chimera.providers import listing

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    option = listing._openrouter_option(
        _index_entry("cache-probe/plain", {"prompt": "0.000003", "completion": "0.000015"})
    )
    assert option is not None
    listing.remember_models([option], home=tmp_path)

    assert listing.known_cache_price("openrouter/cache-probe/plain") == (None, None)
    price = resolve_price("openrouter/cache-probe/plain")
    assert price is not None and price.cache_read_per_m is None and price.cache_write_per_m is None


def test_the_catalogue_carries_cache_rates_into_the_table() -> None:
    """The shipped catalogue is the fallback when the index was never fetched. `claude-opus-5` is
    5.00/25.00, so Anthropic's published multipliers put its reads at 0.50 and writes at 6.25."""
    price = resolve_price("openrouter/anthropic/claude-opus-5")

    assert price is not None
    assert (price.cache_read_per_m, price.cache_write_per_m) == (
        pytest.approx(0.50), pytest.approx(6.25),
    )
