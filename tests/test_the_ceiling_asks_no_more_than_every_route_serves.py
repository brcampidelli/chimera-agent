"""The gateway's completion ceiling is a bound on output, and must not become a choice of provider.

OpenRouter reads ``max_tokens`` as a route filter. Measured on 2026-09-26 for the weak rung of three
presets, `mistral-small-3.2`, whose routes list 16,384 / 16,384 / 26,214 / 32,768 output tokens: at
the 32,000 ceiling every tool-free call went to the one route listing 32,768 (6 of 6), and 3 of the
6 came back 429 from that provider's shared pool with no other route to fall back to; at 16,384 or
with no ``max_tokens`` they went where tool calls go (Venice, 8 of 8, none failed). Tool calls were
served at 32,000 too (8 of 8): when no route qualifies, the filter is dropped, not the call.

So the gateway asks for no more than the catalogue records every route of the model serves
(`CatalogEntry.max_output`), when the caller set no budget. A budget the caller chose is left as
chosen. Fakes only — no network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import Settings, get_settings
from chimera.providers.catalog import CATALOG, max_output_for
from chimera.providers.gateway import Message

WEAK = "openrouter/mistralai/mistral-small-3.2-24b-instruct"
OTHER = "openrouter/vendor/uncatalogued"


def _resp(text: str) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


def _chunks(text: str) -> list[SimpleNamespace]:
    delta = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason="stop")
    return [SimpleNamespace(choices=[choice], usage=None)]


def _gateway(monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]], **env: str) -> Any:
    """A gateway over a fake litellm that records every call; ``FAIL`` names models that 404."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("CHIMERA_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("CHIMERA_COMPLETION_CEILING", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    import litellm

    def fake(**kwargs: Any) -> Any:
        seen.append(kwargs)
        if kwargs["model"] in env.get("FAIL", "").split(","):
            raise RuntimeError("No endpoints found for this model (404)")
        return _chunks("hi") if kwargs.get("stream") else _resp("ok")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway

    return LLMGateway()


HI = [Message(role="user", content="hi")]


def test_the_weak_rung_records_what_every_route_serves() -> None:
    """The data the clamp reads. Below the default ceiling, or it would change nothing."""
    assert max_output_for(WEAK) == 16_384
    assert max_output_for(WEAK) < Settings().completion_ceiling


def test_every_recorded_max_output_is_below_the_default_ceiling() -> None:
    """A figure at or above the ceiling is dead data: it can never lower anything, and it goes
    stale without anyone noticing, because nothing reads it."""
    ceiling = Settings().completion_ceiling
    dead = [e.slug for e in CATALOG if e.max_output is not None and not 0 < e.max_output < ceiling]
    assert not dead, f"max_output that cannot bind: {dead}"


def test_a_model_whose_routes_serve_less_is_asked_for_what_they_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete(HI, model=WEAK)
    assert seen[-1]["max_tokens"] == 16_384


def test_an_uncatalogued_model_still_gets_the_whole_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete(HI, model=OTHER)
    assert seen[-1]["max_tokens"] == 32_000


def test_a_budget_the_caller_chose_goes_out_as_chosen(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete(HI, model=WEAK, max_tokens=30_000)
    assert seen[-1]["max_tokens"] == 30_000


def test_a_ceiling_already_below_the_routes_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen, CHIMERA_COMPLETION_CEILING="4000").complete(HI, model=WEAK)
    assert seen[-1]["max_tokens"] == 4_000


def test_zero_still_means_the_providers_own_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen, CHIMERA_COMPLETION_CEILING="0").complete(HI, model=WEAK)
    assert seen[-1]["max_tokens"] is None


def test_each_fallback_is_bounded_for_its_own_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound was taken once, for the primary, and every fallback inherited it — in both
    directions that is somebody else's number."""
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen, CHIMERA_FALLBACK_MODELS=WEAK, FAIL=OTHER).complete(HI, model=OTHER)
    assert [(c["model"], c["max_tokens"]) for c in seen] == [(OTHER, 32_000), (WEAK, 16_384)]

    seen.clear()
    _gateway(monkeypatch, seen, CHIMERA_FALLBACK_MODELS=OTHER, FAIL=WEAK).complete(HI, model=WEAK)
    assert [(c["model"], c["max_tokens"]) for c in seen] == [(WEAK, 16_384), (OTHER, 32_000)]


def test_the_streaming_path_is_bounded_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).stream_complete(HI, model=WEAK)
    assert seen[-1]["max_tokens"] == 16_384 and seen[-1].get("stream") is True


def test_a_stream_that_falls_back_hands_on_the_callers_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handing on the bound already taken would make it a budget the caller never chose, and the
    batch call's fallback would inherit the primary's number."""
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen, CHIMERA_FALLBACK_MODELS=OTHER, FAIL=WEAK)
    gateway.stream_complete(HI, model=WEAK)
    assert [(c["model"], c["max_tokens"], bool(c.get("stream"))) for c in seen] == [
        (WEAK, 16_384, True),  # the stream, before any output
        (WEAK, 16_384, False),  # the batch call it fell back to
        (OTHER, 32_000, False),  # and that call's own fallback, bounded for its own routes
    ]


def test_the_async_and_plain_stream_paths_are_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen)
    import litellm

    async def fake_async(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return _resp("ok")

    monkeypatch.setattr(litellm, "acompletion", fake_async)
    asyncio.run(gateway.acomplete(HI, model=WEAK))
    assert seen[-1]["max_tokens"] == 16_384
    list(gateway.stream(HI, model=WEAK))
    assert seen[-1]["max_tokens"] == 16_384
