"""Qwen3 Coder's published cache discount reaches receipts without a fetched index."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.fusion import receipts
from chimera.providers import listing
from chimera.providers.catalog import register_catalog_prices


def test_qwen_coder_cached_prompt_uses_the_published_read_price(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setattr(receipts, "_PRICES", [])
    monkeypatch.setattr(listing, "known_price", lambda model: None)
    register_catalog_prices()

    price = receipts.resolve_price("openrouter/qwen/qwen3-coder")

    assert price is not None
    assert receipts.cost_usd(price, 1_000_000, 0, cache_read_tokens=1_000_000) == pytest.approx(0.10)
    assert receipts.cost_usd(price, 1_000_000, 0) == pytest.approx(0.30)
    assert receipts.cost_usd(price, 1_000_000, 100_000, cache_read_tokens=800_000) == pytest.approx(0.24)


@pytest.mark.parametrize(
    "slug,expected_in,expected_out,expected_cache_read",
    [
        ("openrouter/openai/gpt-6-luna", 0.10, 0.50, 0.01),
        ("openrouter/openai/gpt-5.6-luna", 0.20, 1.20, 0.02),
        ("openrouter/openai/gpt-5.5", 5.00, 30.00, 0.50),
        ("openrouter/google/gemini-2.5-flash", 0.30, 2.50, 0.03),
        ("openrouter/google/gemini-3.8-flash", 0.825, 4.125, 0.0825),
        ("openrouter/google/gemini-3.1-pro-preview", 2.00, 12.00, 0.20),
        ("openrouter/qwen/qwen3.7-max", 1.475, 4.425, 0.295),
        ("openrouter/z-ai/glm-4.6", 0.55, 2.20, 0.11),
    ],
)
def test_catalog_models_with_published_cache_read_rates_reach_receipts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    slug: str,
    expected_in: float,
    expected_out: float,
    expected_cache_read: float,
) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setattr(receipts, "_PRICES", [])
    monkeypatch.setattr(listing, "known_price", lambda model: None)
    register_catalog_prices()

    price = receipts.resolve_price(slug)

    assert price is not None
    assert (price.input_per_m, price.output_per_m) == (
        pytest.approx(expected_in),
        pytest.approx(expected_out),
    )
    assert price.cache_read_per_m == pytest.approx(expected_cache_read)
    assert receipts.cost_usd(price, 1_000_000, 0, cache_read_tokens=1_000_000) == pytest.approx(
        expected_cache_read
    )
