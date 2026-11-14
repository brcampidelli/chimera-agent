"""Fable 5.2, Opus 5.5, Sonnet 5.5 and Haiku 5.5: a price of their own, and a request they accept.

Two defects, one cause: the catalogue did not know the 5.x generation, so everything about it fell
through to a neighbour. Pricing matched by SUBSTRING, so ``claude-haiku`` (the Haiku 3.5 row, 0.80/4.0)
priced ``claude-haiku-5-5`` at eight times its real input, ``anthropic/claude-opus-5`` priced
``claude-opus-5.5`` as Opus 5, and ``claude-fable-5-2`` had no row at all, which a spend cap reads as
"price unknown, refuse" and stops a non-critical job. And the gateway sent every model the same
request, ``temperature`` and a thinking switch included, to models Anthropic documents as answering
those with a 400.

Prices were read on 2026-11-14 from Anthropic's pricing page AND OpenRouter's /api/v1/models, which
agreed on every figure. Nothing here calls a model: the wire tests read the request body litellm
builds, with the socket replaced.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from chimera.config import get_settings
from chimera.fusion import receipts
from chimera.fusion.receipts import ModelPrice, cost_usd, resolve_price
from chimera.providers.catalog import CATALOG, ModelCaps, caps_for
from chimera.providers.gateway import Message

HI = [Message(role="user", content="hi")]
FABLE = "openrouter/anthropic/claude-fable-5.2"
OPUS = "openrouter/anthropic/claude-opus-5.5"
SONNET = "openrouter/anthropic/claude-sonnet-5.5"
HAIKU = "openrouter/anthropic/claude-haiku-5.5"

# slug -> (input, output, cache read, cache write 5m), USD per 1M tokens.
PRICES = {
    FABLE: (8.0, 40.0, 0.20, 10.0),
    OPUS: (4.0, 20.0, 0.20, 5.0),
    SONNET: (2.0, 10.0, 0.10, 2.5),
    HAIKU: (0.10, 0.50, 0.01, 0.125),
}


@pytest.fixture
def fresh_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """A private price table, catalogue folded in on the first lookup, and no live index.

    The live index is a file on the machine running the test; with it in play the answer depends on
    what that machine last fetched, and the substring table, the thing under test, is not reached.
    """
    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    import chimera.providers.listing as listing

    monkeypatch.setattr(listing, "known_price", lambda _slug: None)
    monkeypatch.setattr(listing, "known_cache_price", lambda _slug: (None, None))
    monkeypatch.setattr(listing, "known_long_prompt", lambda _slug: None)


# --------------------------------------------------------------------------- prices


@pytest.mark.parametrize("slug", list(PRICES))
def test_each_model_gets_its_own_price(fresh_table: None, slug: str) -> None:
    price = resolve_price(slug)
    assert price is not None, f"{slug} has no price, so a spend cap refuses it"
    i, o, read, write = PRICES[slug]
    assert (price.input_per_m, price.output_per_m) == (i, o)
    assert (price.cache_read_per_m, price.cache_write_per_m) == (read, write)


@pytest.mark.parametrize(
    "spelling",
    [
        "anthropic/claude-haiku-5-5",  # Anthropic's own id through LiteLLM
        "claude-haiku-5-5",
        "anthropic.claude-haiku-5-5",  # Bedrock
        "openrouter/anthropic/claude-haiku-5.5",
        "claude-haiku-5-5-20261007",  # a dated snapshot is still the same model
    ],
)
def test_haiku_5_5_never_gets_the_haiku_3_5_price(fresh_table: None, spelling: str) -> None:
    """The reported defect: ``claude-haiku`` is a substring of every Haiku and priced them all 0.80/4.0."""
    price = resolve_price(spelling)
    assert price is not None
    assert (price.input_per_m, price.output_per_m) == (0.10, 0.50), spelling


@pytest.mark.parametrize("spelling", ["anthropic/claude-haiku-4-5", "openrouter/anthropic/claude-haiku-4.5"])
def test_haiku_4_5_is_priced_at_one_and_five_not_the_haiku_3_5_rate(fresh_table: None, spelling: str) -> None:
    """Haiku 4.5 costs $1 in / $5 out (cache read 0.10, 5m write 1.25: Anthropic's pricing page and
    OpenRouter's index agree). The first version of this test asserted 0.80 / 4.0, which is the Haiku
    3.5 price the generic `claude-haiku` row carries: it preserved the very error it sat beside, and
    under-stated every Haiku 4.5 call by 20%. The generic row stays for 3.x."""
    price = resolve_price(spelling)
    assert price is not None
    assert (price.input_per_m, price.output_per_m) == (1.0, 5.0)
    assert (price.cache_read_per_m, price.cache_write_per_m) == (0.10, 1.25)
    older = resolve_price("anthropic/claude-haiku-3-5")
    assert older is not None and (older.input_per_m, older.output_per_m) == (0.80, 4.0)


def test_opus_5_and_opus_5_5_are_two_prices_in_either_registration_order(
    fresh_table: None,
) -> None:
    """``anthropic/claude-opus-5`` is a substring of ``anthropic/claude-opus-5.5``. Which one a lookup
    finds first used to depend on which was registered later: a position in a list."""
    opus5 = resolve_price("openrouter/anthropic/claude-opus-5")
    opus55 = resolve_price(OPUS)
    assert opus5 is not None and opus55 is not None
    assert (opus5.input_per_m, opus5.output_per_m) == (5.0, 25.0)
    assert (opus55.input_per_m, opus55.output_per_m) == (4.0, 20.0)
    # And the same answer with the registered rows in the opposite order.
    receipts._PRICES.reverse()
    assert resolve_price(OPUS) == opus55
    assert resolve_price("openrouter/anthropic/claude-opus-5") == opus5


def test_the_fable_has_a_price(fresh_table: None) -> None:
    for spelling in ("claude-fable-5-2", "anthropic/claude-fable-5.2", FABLE):
        assert resolve_price(spelling) is not None, spelling


def test_a_prompt_over_100k_pays_the_haiku_long_prompt_tier(fresh_table: None) -> None:
    price = resolve_price(HAIKU)
    assert price is not None and price.long_prompt is not None and price.long_prompt_over == 100_000
    assert (price.long_prompt.input_per_m, price.long_prompt.output_per_m) == (0.50, 2.50)
    short = cost_usd(price, 100_000, 1_000)  # "over" 100,000, so exactly 100,000 is still short
    long = cost_usd(price, 100_001, 1_000)
    assert short == round((100_000 * 0.10 + 1_000 * 0.50) / 1e6, 6)
    assert long == round((100_001 * 0.50 + 1_000 * 2.50) / 1e6, 6)
    # Cache tokens count toward the length and are priced at the long tier's own cache rates.
    cached = cost_usd(price, 200_000, 0, cache_read_tokens=150_000, cache_write_tokens=10_000)
    assert cached == round((40_000 * 0.50 + 150_000 * 0.05 + 10_000 * 0.625) / 1e6, 6)


def test_a_flat_price_is_not_tiered() -> None:
    assert cost_usd(ModelPrice(1.0, 2.0), 500_000, 0) == 0.5


def test_the_long_prompt_tier_survives_the_live_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """The index lists the short price only; a long Haiku call must not price as a short one."""
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_price", lambda _slug: (0.10, 0.50))
    monkeypatch.setattr(listing, "known_cache_price", lambda _slug: (0.01, 0.125))
    monkeypatch.setattr(listing, "known_long_prompt", lambda _slug: None)
    price = resolve_price(HAIKU)
    assert price is not None and price.long_prompt is not None
    assert cost_usd(price, 200_000, 0) == round(200_000 * 0.50 / 1e6, 6)


def test_the_catalogue_rows_carry_the_published_prices() -> None:
    rows = {e.slug: e for e in CATALOG}
    for slug, (i, o, read, write) in PRICES.items():
        row = rows[slug]
        assert (row.input_per_m, row.output_per_m) == (i, o)
        assert (row.cache_read_per_m, row.cache_write_per_m) == (read, write)
    assert rows[HAIKU].long_prompt == (100_000, 0.50, 2.50, 0.05, 0.625)


def test_no_new_model_is_a_default_of_any_preset() -> None:
    from chimera.config import Settings
    from chimera.providers.catalog import _PRESETS

    used = {slug for ladder in _PRESETS.values() for slug in (ladder.weak, ladder.mid, ladder.top)}
    settings = Settings()
    used |= {settings.default_model, *settings.fusion_panel, settings.fusion_judge, settings.fusion_synthesizer}
    assert not used & set(PRICES), used & set(PRICES)


# --------------------------------------------------------------------------- capabilities


def test_capabilities_match_what_anthropic_publishes() -> None:
    assert caps_for(FABLE) == ModelCaps(
        sampling=False, forced_tool_choice=False, thinking_off=False, retention_days=30
    )
    assert caps_for(OPUS).retention_days is None and not caps_for(OPUS).sampling
    assert not caps_for(SONNET).sampling and not caps_for(SONNET).thinking_off
    # Sonnet 5.5 "doesn't support forced tool use" (What's new in Claude Sonnet 5.5): a 400.
    assert not caps_for(SONNET).forced_tool_choice
    assert caps_for(HAIKU).forced_tool_choice
    assert not caps_for(HAIKU).sampling and caps_for(HAIKU).thinking_off


@pytest.mark.parametrize(
    "slug",
    [
        "openrouter/anthropic/claude-opus-5",  # the previous Opus: nothing published, nothing dropped
        "openrouter/anthropic/claude-sonnet-5",
        "openrouter/anthropic/claude-fable-5.1",
        "openrouter/anthropic/claude-haiku-4.5",
        "openrouter/deepseek/deepseek-v4-flash",
        "claude-fable-5-20",  # a different version, not a prefix of 5-2
    ],
)
def test_a_model_with_no_row_is_asked_everything_as_before(slug: str) -> None:
    assert caps_for(slug) == ModelCaps()


def test_other_spellings_of_the_fable_reach_its_row() -> None:
    for slug in ("claude-fable-5-2", "anthropic/claude-fable-5-2", "global.anthropic.claude-fable-5-2"):
        assert caps_for(slug).retention_days == 30, slug


# --------------------------------------------------------------------------- the request


def test_provider_kwargs_never_switch_thinking_off_for_a_model_that_cannot() -> None:
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    gateway = LLMGateway(Settings())
    for slug in (FABLE, OPUS, SONNET):
        kwargs = gateway._provider_kwargs(slug, thinking=False)
        assert "reasoning" not in kwargs.get("extra_body", {}), slug
        for key in ("temperature", "top_p", "top_k", "tool_choice"):
            assert key not in kwargs, (slug, key)
    # A model that CAN skip thinking still gets the switch.
    assert gateway._provider_kwargs(HAIKU, thinking=False)["extra_body"] == {"reasoning": {"enabled": False}}
    deepseek = "openrouter/deepseek/deepseek-v4-flash-0731"
    assert gateway._provider_kwargs(deepseek, thinking=False)["extra_body"] == {"reasoning": {"enabled": False}}


_OK = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
_TOOLS = [
    {"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}
]


def _wire(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every request body litellm puts on the socket, answered with a canned reply."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-" + "test")
    get_settings.cache_clear()
    bodies: list[dict[str, Any]] = []

    def send(self: httpx.Client, request: httpx.Request, *_a: Any, **_k: Any) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, request=request)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_OK, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return bodies


def _call(monkeypatch: pytest.MonkeyPatch, model: str) -> dict[str, Any]:
    from chimera.providers import LLMGateway

    bodies = _wire(monkeypatch)
    LLMGateway().complete(
        HI, model=model, temperature=0.2, thinking=False, tools=_TOOLS,
        top_p=0.9, top_k=5,
    )
    assert len(bodies) == 1
    return bodies[0]


@pytest.mark.parametrize("model", [FABLE, OPUS, SONNET, HAIKU])
def test_the_wire_carries_no_sampling_parameter_for_the_5_generation(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    body = _call(monkeypatch, model)
    for key in ("temperature", "top_p", "top_k"):
        assert key not in body, f"{model} was sent {key}"


@pytest.mark.parametrize("model", [FABLE, OPUS, SONNET])
def test_the_wire_carries_no_thinking_off_for_a_model_that_cannot(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    body = _call(monkeypatch, model)
    assert body.get("reasoning", {}).get("enabled") is not False
    assert body["tools"], "the tools themselves must still be sent"


@pytest.mark.parametrize("model", [FABLE, OPUS, SONNET])
@pytest.mark.parametrize(
    "choice",
    ["required", {"type": "any"}, {"type": "tool", "name": "f"}, {"type": "function", "function": {"name": "f"}}],
)
def test_a_forced_tool_call_is_refused_loudly_not_turned_into_auto(
    monkeypatch: pytest.MonkeyPatch, model: str, choice: Any
) -> None:
    """The provider answers a forced call with a 400. Dropping the parameter (or sending `auto`)
    would let a caller that demanded `emit_result` get plain text back as a success."""
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import UnsupportedParameterError

    bodies = _wire(monkeypatch)
    with pytest.raises(UnsupportedParameterError, match="forced tool_choice"):
        LLMGateway().complete(HI, model=model, tools=_TOOLS, tool_choice=choice)
    assert bodies == [], "nothing may reach the wire"


def test_a_forced_tool_call_hidden_in_extra_body_is_refused_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import UnsupportedParameterError

    _wire(monkeypatch)
    with pytest.raises(UnsupportedParameterError):
        LLMGateway().complete(HI, model=FABLE, tools=_TOOLS, extra_body={"tool_choice": "required"})


def test_a_forced_choice_is_still_sent_to_a_model_that_accepts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import LLMGateway

    bodies = _wire(monkeypatch)
    LLMGateway().complete(HI, model=HAIKU, tools=_TOOLS, tool_choice="required")
    assert bodies[0]["tool_choice"] == "required"


def test_a_non_forcing_tool_choice_is_kept_for_the_fable(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import LLMGateway

    bodies = _wire(monkeypatch)
    LLMGateway().complete(HI, model=FABLE, tools=_TOOLS, tool_choice="auto")
    assert bodies[0]["tool_choice"] == "auto"


def test_a_model_with_no_restriction_is_sent_exactly_what_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the same call to a model with no row still carries every parameter."""
    body = _call(monkeypatch, "openrouter/deepseek/deepseek-v4-flash-0731")
    assert body["temperature"] == 0.2 and body["top_p"] == 0.9
    assert body["reasoning"] == {"enabled": False}


def test_the_filters_decide_per_model() -> None:
    from chimera.providers.gateway import _sampling, _without_refused

    assert _sampling(FABLE, 0.2) == {} and _sampling("openrouter/x/y", 0.2) == {"temperature": 0.2}
    assert _without_refused(FABLE, {"top_p": 1, "n": 1}) == {"n": 1}
    assert _without_refused(FABLE, {"tool_choice": {"type": "auto"}}) == {"tool_choice": {"type": "auto"}}
    assert _without_refused("openrouter/x/y", {"top_p": 1, "tool_choice": "required"}) == {
        "top_p": 1, "tool_choice": "required",
    }


# --------------------------------------------------------------------------- the privacy card


def test_the_privacy_card_names_the_model_that_requires_retention() -> None:
    from chimera.config import Settings
    from chimera.providers.privacy import privacy_snapshot, retention_required

    default = Settings(CHIMERA_DEFAULT_MODEL=FABLE)  # type: ignore[call-arg]
    rows = retention_required(default)
    assert [r["model"] for r in rows] == [FABLE]
    assert rows[0]["days"] == 30 and "default" in rows[0]["roles"] and rows[0]["conflicts"] is False
    asked = Settings(CHIMERA_DEFAULT_MODEL=FABLE, CHIMERA_OPENROUTER_DATA_COLLECTION="deny")  # type: ignore[call-arg]
    assert retention_required(asked)[0]["conflicts"] is True
    assert privacy_snapshot(asked)["retention_required"][0]["model"] == FABLE
    # Shipped defaults use no such model, so the card says nothing about retention.
    assert retention_required(Settings()) == []
