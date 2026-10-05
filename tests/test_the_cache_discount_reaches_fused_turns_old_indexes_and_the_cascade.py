"""The cache discount reaches the three paths the first fix missed.

Pricing the prompt's cache share at its own rate (the receipt test beside this one) only helps where
the cache counts arrive and the cache rates resolve. Three places dropped one or the other:

- A FUSED turn is priced by its stages, and the stages carried no cache counts — so the Fuse
  button, the most expensive thing the app does, still billed every cached prompt at the input rate.
- An index file written before cache rates were parsed (``model-prices.json`` outlives upgrades)
  shadowed the catalogue's rates with "unknown", so ``claude-opus-5`` read at 1x on every install
  that had fetched the index once, until the next warm-up rewrote it.
- The cascade's weak-tier consensus summed the prompt of all k samples but kept ONE sample's cache
  reads, so k-1 samples' reads were billed at the input rate again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.fusion import FusionConfig, FusionEngine
from chimera.fusion.cascade import CascadeBackend, CascadeConfig
from chimera.fusion.engine import StageUsage
from chimera.fusion.receipts import ModelPrice, price_stage, resolve_price, set_price
from chimera.fusion.router import RoutingPolicy
from chimera.orchestration.receipts import price_completion
from chimera.providers import CompletionResult

MODEL = "openrouter/cache-probe/fused-qq"
#: Anthropic's published shape: reads 0.1x, five-minute writes 1.25x of input.
CACHED = ModelPrice(3.0, 15.0, cache_read_per_m=0.30, cache_write_per_m=3.75)
#: One cached call: 10,000 prompt tokens of which 9,000 were read from the cache.
ONE_CALL_USD = (1_000 * 3.0 + 9_000 * 0.30) / 1_000_000


class _CachedBackend:
    """Every model answers with the same cached usage, whatever stage asked."""

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        return CompletionResult(
            content=f"answer from {model}", model=str(model), prompt_tokens=10_000,
            completion_tokens=0, cache_read_tokens=9_000, cache_write_tokens=0,
        )


def test_a_fused_turn_prices_each_stage_with_its_cache_reads() -> None:
    """Panel x2, judge and synthesiser: four cached calls, each priced like a single cached call."""
    set_price(MODEL, CACHED)
    config = FusionConfig(panel=[MODEL, MODEL], judge=MODEL, synthesizer=MODEL)

    result = FusionEngine(_CachedBackend(), config).complete([{"role": "user", "content": "hi"}])
    cost = price_completion(result)

    assert cost.unpriced is None
    assert cost.usd == pytest.approx(4 * ONE_CALL_USD)
    assert cost.usd < 4 * 10_000 * 3.0 / 1_000_000, "the fused turn billed its cache reads at 1x"


def test_a_fusion_receipt_prices_a_stage_with_its_cache_reads() -> None:
    """`price_stage` is the itemised receipt behind selective fusion; it bills the same way."""
    set_price(MODEL, CACHED)

    cost = price_stage(StageUsage("panel", MODEL, 10_000, 0, cache_read_tokens=9_000))

    assert cost.usd == pytest.approx(ONE_CALL_USD)


def _write_index(home: Path, models: dict[str, dict[str, Any]]) -> None:
    """A `model-prices.json` as an install wrote it BEFORE cache rates were parsed: no cache keys."""
    payload = {"fetched_at": "2026-10-01T00:00:00+00:00", "models": models}
    (home / "model-prices.json").write_text(json.dumps(payload), encoding="utf-8")


def test_an_index_written_before_cache_rates_does_not_hide_the_catalogues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    _write_index(tmp_path, {"openrouter/anthropic/claude-opus-5": {"in": 5.0, "out": 25.0}})

    price = resolve_price("openrouter/anthropic/claude-opus-5")

    assert price is not None
    assert (price.input_per_m, price.output_per_m) == (5.0, 25.0)
    assert price.cache_read_per_m == pytest.approx(0.50)
    assert price.cache_write_per_m == pytest.approx(6.25)


def test_a_cache_rate_is_not_borrowed_across_a_different_price(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the index prices the slug differently from our row, the row describes another price point,
    and its cache rate would be billed against an input rate it was never published beside."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    _write_index(tmp_path, {"openrouter/anthropic/claude-opus-5": {"in": 4.0, "out": 20.0}})

    price = resolve_price("openrouter/anthropic/claude-opus-5")

    assert price is not None and (price.input_per_m, price.output_per_m) == (4.0, 20.0)
    assert price.cache_read_per_m is None and price.cache_write_per_m is None


def test_the_index_cache_rate_still_wins_over_the_catalogue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    _write_index(
        tmp_path,
        {"openrouter/anthropic/claude-opus-5": {"in": 5.0, "out": 25.0, "cache_read": 0.4}},
    )

    price = resolve_price("openrouter/anthropic/claude-opus-5")

    assert price is not None
    assert price.cache_read_per_m == pytest.approx(0.4)  # published for this slug: it wins
    assert price.cache_write_per_m == pytest.approx(6.25)  # silent there: the catalogue fills it


def test_the_cascade_consensus_reports_the_cache_reads_of_every_sample(tmp_path: Path) -> None:
    weak = "cache-probe/weak-qq"

    class _AgreeingCached:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
            self.calls += 1
            return CompletionResult(
                content="the same agreed answer", model=weak, prompt_tokens=1_000,
                completion_tokens=5, cache_read_tokens=900, cache_write_tokens=10,
            )

    class _NoFusion:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            raise AssertionError("the weak tier agreed; fusion must not run")

    gateway = _AgreeingCached()
    config = CascadeConfig(weak=weak, mid="cache-probe/mid-qq", agreement_k=3,
                           log_path=tmp_path / "routes.jsonl")
    backend = CascadeBackend(gateway, _NoFusion(), config, policy=RoutingPolicy(mode="auto"))  # type: ignore[arg-type]  # the duck-typed fakes the module's own tests use

    result = backend.complete([{"role": "user", "content": "easy"}])

    assert gateway.calls == 3
    assert result.prompt_tokens == 3_000
    assert result.cache_read_tokens == 2_700, "k prompts summed beside one sample's cache reads"
    assert result.cache_write_tokens == 30
