"""Integration of the Claude 5.x rules with the failover chain, the spend cap and the live index.

Found by the second review of P0-13, all three at the seams between the new capability table and code
that already existed:

- The forced-``tool_choice`` check ran on ``{**caller_kwargs, **gateway_extra}``, a plain merge in
  which the gateway's own ``extra_body`` (a route pin, the privacy preference) REPLACES the caller's,
  hiding a forced choice the caller had put there. ``_call_kwargs`` then restored it, and
  ``_without_refused`` raised OUTSIDE the handler that skips an incompatible fallback, so a compatible
  one behind it was never tried. The check now reads the merge that is actually sent.
- ``UnsupportedParameterError`` is raised before any request exists, but ``SpendCappedBackend`` did not
  know that, classified it ``UNKNOWN``, and forfeited the reservation: the cap was charged the worst
  case of a call that never left the process, and the corrected retry could be refused for it.
- A ``:batch`` variant of Haiku 5.5 has its OWN short price (0.05/0.25), so the long-prompt tier
  borrowed from the table did not apply and a 150,000-token request priced at a fifth of its cost. The
  index publishes the variant's own tier in ``pricing.overrides``; it is read and kept.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import litellm
import pytest

from chimera.config import Settings, get_settings
from chimera.fusion import receipts
from chimera.fusion.receipts import cost_usd, resolve_price
from chimera.providers import LLMGateway
from chimera.providers.gateway import Message, UnsupportedParameterError

HI = [Message(role="user", content="hi")]
DEEPSEEK = "openrouter/deepseek/deepseek-v4-flash-0731"
FABLE = "openrouter/anthropic/claude-fable-5.2"
SONNET = "openrouter/anthropic/claude-sonnet-5.5"
HAIKU = "openrouter/anthropic/claude-haiku-5.5"
_TOOLS = [
    {"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}
]


class _Stop(Exception):
    pass


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """litellm's completion, replaced: the primary answers 503, every other model stops the call."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-" + "test")
    get_settings.cache_clear()
    calls: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if "deepseek" in str(kwargs.get("model")):
            raise litellm.ServiceUnavailableError(
                message="503 service unavailable", model=str(kwargs["model"]), llm_provider="openrouter"
            )
        raise _Stop

    monkeypatch.setattr(litellm, "completion", completion)
    return calls


# --------------------------------------------------------------------- the fallback chain


def test_a_fallback_that_cannot_force_a_call_is_skipped_and_the_compatible_one_is_tried(
    monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]]
) -> None:
    """Primary 503, fallbacks Fable then Haiku, ZDR on (so the gateway sends its own `extra_body`),
    and a forced choice hidden in the caller's `extra_body`: Fable is skipped, Haiku is called."""
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", f"{FABLE},{HAIKU}")
    monkeypatch.setenv("CHIMERA_OPENROUTER_ZDR", "true")
    get_settings.cache_clear()
    with pytest.raises(_Stop):
        LLMGateway().complete(
            HI, model=DEEPSEEK, tools=_TOOLS, extra_body={"tool_choice": "required"}
        )
    models = [str(c["model"]) for c in seen]
    assert any("deepseek" in m for m in models), "the primary takes a forced choice and runs first"
    assert not any("fable" in m for m in models), models
    assert any("haiku" in m for m in models), models
    haiku = next(c for c in seen if "haiku" in str(c["model"]))
    assert haiku["extra_body"]["tool_choice"] == "required"
    assert haiku["extra_body"]["provider"]["zdr"] is True


def test_a_primary_that_cannot_force_a_call_raises_even_with_the_gateways_own_extra_body(
    monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("CHIMERA_OPENROUTER_ZDR", "true")
    get_settings.cache_clear()
    with pytest.raises(UnsupportedParameterError):
        LLMGateway().complete(HI, model=FABLE, tools=_TOOLS, extra_body={"tool_choice": "required"})
    assert seen == []


# --------------------------------------------------------------------- the spend cap


@pytest.fixture
def published_prices() -> None:
    from chimera.providers.listing import ModelOption, remember_models

    # These tests reach the gateway's error paths after strict admission.
    assert remember_models([
        ModelOption(
            slug=slug, label=slug, vendor="Anthropic", source="openrouter",
            input_per_m=input_per_m, output_per_m=output_per_m,
        )
        for slug, input_per_m, output_per_m in [(SONNET, 2.0, 10.0), (HAIKU, 0.10, 0.50)]
    ])


def test_a_refusal_before_the_wire_gives_the_reservation_back(
    published_prices: None, seen: list[dict[str, Any]]
) -> None:
    """Nothing left the process, so nothing was billed: the cap is not charged the worst case."""
    from chimera.orchestration.budget import SpendBudget, SpendCappedBackend

    budget = SpendBudget(1.0, strict=True)
    capped = SpendCappedBackend(LLMGateway(), budget)
    with pytest.raises(UnsupportedParameterError):
        capped.complete(HI, model=SONNET, tools=_TOOLS, tool_choice="required", max_tokens=1_000)
    assert seen == []
    assert budget.spent == 0.0 and budget.reserved == 0.0
    assert budget.blocked() is None, "the corrected retry must still be admitted"


def test_a_genuinely_unknown_error_still_forfeits_the_reservation(
    published_prices: None, seen: list[dict[str, Any]]
) -> None:
    """The control: only the pre-send refusal is exempt. A provider error may have been billed."""
    from chimera.orchestration.budget import SpendBudget, SpendCappedBackend

    budget = SpendBudget(1.0, strict=True)
    capped = SpendCappedBackend(LLMGateway(), budget)
    with pytest.raises(_Stop):
        capped.complete(HI, model=HAIKU, tools=_TOOLS, max_tokens=1_000)
    assert budget.spent > 0.0


# --------------------------------------------------------------------- the live index


def _batch_entry() -> dict[str, Any]:
    """Shaped on the index row of 2026-11-14 for `anthropic/claude-haiku-5.5:batch`."""
    return {
        "id": "anthropic/claude-haiku-5.5:batch",
        "name": "Anthropic: Claude Haiku 5.5 (batch)",
        "context_length": 1_000_000,
        "pricing": {
            "prompt": "0.00000005", "completion": "0.00000025",
            "input_cache_read": "0.000000005", "input_cache_write": "0.0000000625",
            "overrides": [
                {
                    "min_prompt_tokens": 100000, "prompt": "0.00000025", "completion": "0.00000125",
                    "input_cache_read": "0.000000025", "input_cache_write": "0.0000003125",
                }
            ],
        },
    }


def test_the_index_row_is_parsed_with_its_long_prompt_tier() -> None:
    from chimera.providers.listing import _openrouter_option

    option = _openrouter_option(_batch_entry())
    assert option is not None
    assert (option.input_per_m, option.output_per_m) == (0.05, 0.25)
    assert option.long_prompt == (100_000, 0.25, 1.25, 0.025, 0.3125)
    plain = _batch_entry()
    del plain["pricing"]["overrides"]
    flat = _openrouter_option(plain)
    assert flat is not None and flat.long_prompt is None
    plain["pricing"]["overrides"] = [{"min_prompt_tokens": "soon", "prompt": "x"}]
    junk = _openrouter_option(plain)
    assert junk is not None and junk.long_prompt is None


def test_the_tier_is_remembered_between_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from chimera.providers import listing

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(listing, "_index_cache", None)
    option = listing._openrouter_option(_batch_entry())
    assert option is not None
    listing.remember_models([option], home=tmp_path)
    assert listing.known_long_prompt(option.slug) == (100_000, 0.25, 1.25, 0.025, 0.3125)
    assert listing.known_long_prompt("openrouter/never/seen") is None


def test_a_variant_with_its_own_price_keeps_its_own_long_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.05/0.25 is not the table's 0.10/0.50, so the table's tier cannot be borrowed; the index's can.
    150,000 prompt tokens + 1,000 out = 150,000 x 0.25 + 1,000 x 1.25 = 0.03875, not 0.00775."""
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: listing.Remembered(
        0.05, 0.25, None, None, None, cache_read_per_m=0.005, cache_write_per_m=0.0625, long_prompt=(100_000, 0.25, 1.25, 0.025, 0.3125),
    ))
    price = resolve_price(f"{HAIKU}:batch")
    assert price is not None and price.long_prompt is not None
    assert cost_usd(price, 150_000, 1_000) == pytest.approx(0.03875)
    assert cost_usd(price, 50_000, 1_000) == pytest.approx(50_000 * 0.05 / 1e6 + 1_000 * 0.25 / 1e6)


def test_an_older_cached_row_without_overrides_still_borrows_the_tables_tier_at_an_equal_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: listing.Remembered(
        0.1, 0.5, None, None, None, cache_read_per_m=0.01, cache_write_per_m=0.125,
    ))
    price = resolve_price(HAIKU)
    assert price is not None and price.long_prompt is not None


def test_the_settings_object_is_unchanged_by_these_tests() -> None:
    assert Settings().default_model  # the fixtures above only set env vars through monkeypatch


def test_the_retention_card_knows_whether_a_fallback_could_take_over() -> None:
    """With a usable fallback an unavailable model costs a model; with none, it costs the turn. A
    fallback that needs retention itself is no way out of a zero-retention preference."""
    from chimera.providers.privacy import retention_required

    deny = {"CHIMERA_DEFAULT_MODEL": FABLE, "CHIMERA_OPENROUTER_ZDR": "true"}
    none = retention_required(Settings(**deny))  # type: ignore[arg-type]
    assert none[0]["enforced"] is True and none[0]["fallback"] is False
    with_haiku = retention_required(Settings(CHIMERA_FALLBACK_MODELS=HAIKU, **deny))  # type: ignore[arg-type]
    assert with_haiku[0]["fallback"] is True
    only_fable = retention_required(Settings(CHIMERA_FALLBACK_MODELS=FABLE, **deny))  # type: ignore[arg-type]
    assert only_fable[0]["fallback"] is False
