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
