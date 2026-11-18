"""The 5.x rules follow the MODEL: through ``extra_body``, behind an alias, past an endpoint name, per call.

The first pass at the Claude 5.x catalogue (``test_the_claude_5_generation_is_priced_and_asked_only_
what_it_accepts.py``) read the request at one door, the top-level kwargs of a synchronous completion.
Review found the other doors. ``extra_body`` is spliced into the request body verbatim, and the
gateway's own thinking-off switch travels in it, so ``temperature``, ``top_p``, ``top_k``,
``tool_choice`` and ``reasoning.enabled=False`` all came back out through it. OpenRouter's
``~anthropic/claude-*-latest`` aliases matched no row, so the alias of Haiku 5.5 priced at 0.80/4.00 and
the alias of Fable got no caps and no retention warning. An ``endpoint/claude-fable-5-2/qwen3`` slug
was read as Fable because its endpoint NAME contained the id. And a cascade's k samples were priced
as one prompt k times as long, so Haiku 5.5 at 60k per call and k=2 read as a 120k call.

Every test that reads a request does it on all four paths that send one: ``complete``, ``acomplete``,
``stream`` and ``stream_complete``. No call is paid: litellm's entry points are replaced by a stub that
records what it was handed and stops.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

import pytest

from chimera.config import Settings, get_settings
from chimera.fusion import receipts
from chimera.fusion.receipts import cost_usd, resolve_price
from chimera.providers import LLMGateway
from chimera.providers.catalog import caps_for, model_identity
from chimera.providers.gateway import CompletionResult, Message

HI = [Message(role="user", content="hi")]
FABLE = "openrouter/anthropic/claude-fable-5.2"
OPUS = "openrouter/anthropic/claude-opus-5.5"
SONNET = "openrouter/anthropic/claude-sonnet-5.5"
HAIKU = "openrouter/anthropic/claude-haiku-5.5"
FABLE_LATEST = "openrouter/~anthropic/claude-fable-latest"
HAIKU_LATEST = "openrouter/~anthropic/claude-haiku-latest"


class _Stop(Exception):
    """Raised by the stub so nothing past the request is exercised."""


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every kwargs dict litellm's completion entry points were called with."""
    import litellm

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-" + "test")
    get_settings.cache_clear()
    calls: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise _Stop

    async def acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise _Stop

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(litellm, "acompletion", acompletion)
    return calls


#: What a caller (or a bench) can put in ``extra_body``: every parameter these models answer with a 400.
_EXTRA: dict[str, Any] = {
    "temperature": 0.3,
    "top_p": 0.8,
    "top_k": 4,
    "reasoning": {"enabled": False},
    "thinking": {"type": "disabled"},
    "keep_me": 1,
}


def _run_complete(gw: LLMGateway, model: str, **kw: Any) -> None:
    gw.complete(HI, model=model, temperature=0.2, thinking=False, max_tokens=64, **kw)


def _run_acomplete(gw: LLMGateway, model: str, **kw: Any) -> None:
    asyncio.run(gw.acomplete(HI, model=model, temperature=0.2, thinking=False, max_tokens=64, **kw))


def _run_stream(gw: LLMGateway, model: str, **kw: Any) -> None:
    list(gw.stream(HI, model=model, temperature=0.2, max_tokens=64, **kw))


def _run_stream_complete(gw: LLMGateway, model: str, **kw: Any) -> None:
    gw.stream_complete(HI, model=model, temperature=0.2, thinking=False, max_tokens=64, **kw)


PATHS: list[Callable[..., None]] = [_run_complete, _run_acomplete, _run_stream, _run_stream_complete]
PATH_IDS = ["complete", "acomplete", "stream", "stream_complete"]


def _invoke(path: Callable[..., None], model: str, **kw: Any) -> None:
    try:
        path(LLMGateway(Settings()), model, **kw)
    except _Stop:
        pass
    except Exception:
        pass


@pytest.mark.parametrize("path", PATHS, ids=PATH_IDS)
@pytest.mark.parametrize("model", [FABLE, OPUS, SONNET])
def test_extra_body_does_not_send_back_what_the_model_refuses(
    seen: list[dict[str, Any]], path: Callable[..., None], model: str
) -> None:
    _invoke(path, model, extra_body=dict(_EXTRA), top_p=0.9, top_k=5)
    assert seen, "the path never reached litellm"
    for kwargs in seen:
        for key in ("temperature", "top_p", "top_k"):
            assert key not in kwargs, f"{model}: top-level {key}"
        body = kwargs.get("extra_body", {})
        for key in ("temperature", "top_p", "top_k", "thinking"):
            assert key not in body, f"{model}: extra_body {key}"
        assert body.get("reasoning", {}).get("enabled") is not False, f"{model}: reasoning.enabled=False"
        assert body["keep_me"] == 1, "what the model accepts must survive"


@pytest.mark.parametrize("path", PATHS, ids=PATH_IDS)
def test_extra_body_keeps_everything_for_a_model_with_no_restriction(
    seen: list[dict[str, Any]], path: Callable[..., None]
) -> None:
    """The control: the same call to an unrestricted model still carries every key."""
    _invoke(path, "openrouter/deepseek/deepseek-v4-flash-0731", extra_body=dict(_EXTRA), top_p=0.9)
    assert seen
    kwargs = seen[0]
    assert kwargs["temperature"] == 0.2 and kwargs["top_p"] == 0.9
    assert kwargs["extra_body"]["top_k"] == 4 and kwargs["extra_body"]["reasoning"] == {"enabled": False}


@pytest.mark.parametrize("path", PATHS, ids=PATH_IDS)
def test_an_effort_survives_the_removal_of_the_thinking_switch(
    seen: list[dict[str, Any]], path: Callable[..., None]
) -> None:
    """``reasoning: {enabled: False, effort: high}``: only the part the model refuses comes out."""
    _invoke(path, FABLE, extra_body={"reasoning": {"enabled": False, "effort": "high"}})
    assert seen[0]["extra_body"]["reasoning"] == {"effort": "high"}


@pytest.mark.parametrize("path", PATHS, ids=PATH_IDS)
@pytest.mark.parametrize("model", [FABLE, OPUS, SONNET])
def test_a_forced_tool_call_stops_every_path_before_the_wire(
    seen: list[dict[str, Any]], path: Callable[..., None], model: str
) -> None:
    from chimera.providers.gateway import UnsupportedParameterError

    with pytest.raises(UnsupportedParameterError, match="forced tool_choice"):
        path(LLMGateway(Settings()), model, tool_choice={"type": "tool", "name": "emit_result"})
    assert seen == []


@pytest.mark.parametrize("path", PATHS, ids=PATH_IDS)
def test_a_choice_that_is_not_forced_still_goes_out(
    seen: list[dict[str, Any]], path: Callable[..., None]
) -> None:
    _invoke(path, FABLE, tool_choice="auto")
    assert seen and seen[0]["tool_choice"] == "auto"


def test_a_fallback_that_cannot_force_a_call_is_skipped_not_called(
    monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]]
) -> None:
    """A forced call whose primary fails must not be quietly served by a fallback that would ignore it."""
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", FABLE)
    get_settings.cache_clear()
    _invoke(_run_complete, "openrouter/deepseek/deepseek-v4-flash-0731", tool_choice="required")
    models = [c["model"] for c in seen]
    assert models and all("fable" not in m for m in models), models


# ---------------------------------------------------------------- aliases


@pytest.fixture
def fresh_table(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: None)


def test_the_latest_aliases_reach_the_caps_of_the_model_they_are() -> None:
    assert caps_for(FABLE_LATEST) == caps_for(FABLE) and caps_for(FABLE_LATEST).retention_days == 30
    assert caps_for("openrouter/~anthropic/claude-opus-latest") == caps_for(OPUS)
    assert caps_for("openrouter/~anthropic/claude-sonnet-latest") == caps_for(SONNET)
    assert caps_for(HAIKU_LATEST) == caps_for(HAIKU) and not caps_for(HAIKU_LATEST).sampling


def test_the_haiku_alias_is_not_priced_as_haiku_3_5_and_keeps_its_long_tier(fresh_table: None) -> None:
    price = resolve_price(HAIKU_LATEST)
    assert price is not None and (price.input_per_m, price.output_per_m) == (0.10, 0.50)
    assert price.long_prompt is not None and price.long_prompt_over == 100_000
    assert cost_usd(price, 200_000, 0) == round(200_000 * 0.50 / 1e6, 6)
    fable = resolve_price(FABLE_LATEST)
    assert fable is not None and (fable.input_per_m, fable.output_per_m) == (8.0, 40.0)


def test_the_haiku_alias_keeps_its_long_tier_through_the_live_index(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.providers.listing as listing

    monkeypatch.setattr(receipts, "_PRICES", list(receipts._PRICES))
    monkeypatch.setattr(receipts, "_catalog_registered", False)
    monkeypatch.setattr(listing, "known_model", lambda _slug: listing.Remembered(
        0.1, 0.5, None, None, None, cache_read_per_m=0.01, cache_write_per_m=0.125,
    ))
    price = resolve_price(HAIKU_LATEST)
    assert price is not None and price.long_prompt is not None


@pytest.mark.parametrize("path", PATHS, ids=PATH_IDS)
def test_the_fable_alias_is_asked_only_what_the_fable_accepts(
    seen: list[dict[str, Any]], path: Callable[..., None]
) -> None:
    _invoke(path, FABLE_LATEST, extra_body=dict(_EXTRA), top_p=0.9)
    assert seen
    assert all("temperature" not in k and "top_p" not in k for k in seen)


def test_the_alias_carries_the_retention_warning() -> None:
    from chimera.providers.privacy import retention_required

    rows = retention_required(Settings(CHIMERA_DEFAULT_MODEL=FABLE_LATEST))  # type: ignore[call-arg]
    assert [r["days"] for r in rows] == [30]


# ---------------------------------------------------------------- identity, not spelling


@pytest.mark.parametrize(
    "slug",
    [
        "endpoint/claude-fable-5-2/qwen3",
        "endpoint/claude-fable-5-2/llama-3.3-70b",
        "endpoint/opus-5-5/claude-haiku-4-5",
        "endpoint/claude-fable-5-2",  # an endpoint name and no model at all
        "openrouter/some-org/not-claude-fable-5-2-clone",
        "ollama_chat/claude-fable-5-2-distill-x",
    ],
)
def test_a_name_in_the_routing_part_does_not_give_a_model_the_rules_of_that_name(slug: str) -> None:
    from chimera.providers.catalog import ModelCaps

    assert caps_for(slug) == ModelCaps(), (slug, model_identity(slug))


def test_an_endpoint_that_serves_the_fable_is_the_fable() -> None:
    """The model part still decides, whatever the endpoint is called."""
    assert caps_for("endpoint/my-proxy/claude-fable-5-2").retention_days == 30
    assert model_identity("endpoint/my-proxy/claude-fable-5-2") == "claude-fable-5-2"


@pytest.mark.parametrize("path", PATHS, ids=PATH_IDS)
def test_the_wire_for_a_misnamed_endpoint_is_untouched(
    monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]], path: Callable[..., None]
) -> None:
    """An endpoint NAMED like the Fable that serves a Qwen: the Qwen keeps its `top_p`."""
    import json

    monkeypatch.setenv("LAB_API_KEY", "lab-" + "test")
    gw = LLMGateway(
        Settings(  # type: ignore[call-arg]
            CHIMERA_ENDPOINTS=json.dumps(
                [{"name": "claude-fable-5-2", "base_url": "https://llm.example/v1", "key_env": "LAB_API_KEY"}]
            )
        )
    )
    with contextlib.suppress(Exception):  # only what reached litellm matters
        path(gw, "endpoint/claude-fable-5-2/qwen3", top_p=0.9)
    assert seen and seen[0].get("top_p") == 0.9 and seen[0].get("temperature") == 0.2


# ---------------------------------------------------------------- a tier is read per call


def test_k_short_calls_are_not_one_long_one(fresh_table: None) -> None:
    """Haiku 5.5, k=2 samples of a 60,000-token prompt: two short requests, not one of 120,000."""
    price = resolve_price(HAIKU)
    assert price is not None
    # 2 calls x (60,000 in at 0.10 + 1,000 out at 0.50) per million
    expected = round(2 * (60_000 * 0.10 + 1_000 * 0.50) / 1e6, 6)
    assert expected == 0.013
    assert cost_usd(price, 120_000, 2_000, calls=2) == expected
    # What the sum alone claims, and what the review measured as the bug: the long tier on the total.
    assert cost_usd(price, 120_000, 2_000) == round((120_000 * 0.50 + 2_000 * 2.50) / 1e6, 6) == 0.065
    # A genuinely long call, repeated, is still long.
    assert cost_usd(price, 240_000, 0, calls=2) == round(240_000 * 0.50 / 1e6, 6)


def test_the_cascade_reports_its_samples_as_calls_and_the_meter_prices_them_that_way(
    fresh_table: None,
) -> None:
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig
    from chimera.orchestration.receipts import price_completion

    class Backend:
        def complete(self, messages: Any, *, model: str | None = None, **_: Any) -> CompletionResult:
            return CompletionResult(
                content="the same answer", model=HAIKU, prompt_tokens=60_000, completion_tokens=1_000,
                finish_reason="stop",
            )

    cascade = CascadeBackend(
        Backend(),  # type: ignore[arg-type]
        Backend(),  # type: ignore[arg-type]
        CascadeConfig(weak=HAIKU, mid=HAIKU, agreement_k=2),
    )
    result = cascade.complete(HI, model=None, temperature=0.7)
    assert result.prompt_tokens == 120_000 and result.calls == 2
    assert price_completion(result).usd == 0.013


def test_a_result_that_is_one_call_is_priced_as_before(fresh_table: None) -> None:
    from chimera.orchestration.receipts import price_completion

    one = CompletionResult(content="x", model=HAIKU, prompt_tokens=120_000, completion_tokens=100)
    assert one.calls is None
    assert price_completion(one).usd == round((120_000 * 0.50 + 100 * 2.50) / 1e6, 6)


# ---------------------------------------------------------------- retention: conflict vs enforced


def test_the_warning_separates_a_preference_in_conflict_from_one_that_is_enforced() -> None:
    from chimera.providers.privacy import retention_required

    deny = {"CHIMERA_OPENROUTER_DATA_COLLECTION": "deny"}
    via_openrouter = retention_required(Settings(CHIMERA_DEFAULT_MODEL=FABLE, **deny))  # type: ignore[arg-type]
    assert via_openrouter[0]["conflicts"] is True and via_openrouter[0]["enforced"] is True
    # Reached directly: the gateway never sends the preference there, so the model runs and the
    # prompt is kept. A warning that promised a failure would be false.
    direct = retention_required(Settings(CHIMERA_DEFAULT_MODEL="anthropic/claude-fable-5-2", **deny))  # type: ignore[arg-type]
    assert direct[0]["conflicts"] is True and direct[0]["enforced"] is False
    # And nothing asked: neither.
    quiet = retention_required(Settings(CHIMERA_DEFAULT_MODEL="anthropic/claude-fable-5-2"))  # type: ignore[call-arg]
    assert quiet[0]["conflicts"] is False and quiet[0]["enforced"] is False


def test_the_gateway_really_does_not_send_the_preference_to_a_direct_route(
    seen: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fact `enforced` rests on, read off the request rather than assumed."""
    monkeypatch.setenv("CHIMERA_OPENROUTER_DATA_COLLECTION", "deny")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "test")
    get_settings.cache_clear()
    _invoke(_run_complete, "anthropic/claude-fable-5-2")
    _invoke(_run_complete, FABLE)
    direct, routed = seen[0], seen[1]
    assert "provider" not in direct.get("extra_body", {})
    assert routed["extra_body"]["provider"]["data_collection"] == "deny"
