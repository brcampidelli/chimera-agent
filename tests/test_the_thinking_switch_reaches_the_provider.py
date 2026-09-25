"""`thinking=False` asks a reasoning model not to think first — and until 2026-09-25 it only got there
on one path. `LLMGateway.complete` and `acomplete` accepted the argument and dropped it; only
`stream_complete` forwarded it. So every blocking call that asked for reasoning off ran with the
model's default: `chimera.decisions.hosted`, the agent loop without a token callback, and both
`bench/jev_decisions` runners (whose "thinking off" arms were, therefore, reasoning).

The second half is the same mechanism one level down: a caller that passed an `extra_body` of its own
replaced the gateway's whole, so the reasoning switch and a configured provider pin vanished the
moment a bench pinned its own route. Fakes only — no network."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.providers.gateway import Message

OR_MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
OFF = {"reasoning": {"enabled": False}}
PIN = {"order": ["DeepInfra"], "allow_fallbacks": False}


def _resp(text: str = "ok") -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def _gateway(monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]], **env: str) -> Any:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    import litellm

    def fake(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        return _resp()

    async def afake(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        return _resp()

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.setattr(litellm, "acompletion", afake)
    from chimera.providers import LLMGateway

    return LLMGateway()


HI = [Message(role="user", content="hi")]


def test_a_blocking_call_that_asks_for_reasoning_off_sends_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete(HI, model=OR_MODEL, thinking=False)
    assert seen[-1]["extra_body"] == OFF


def test_the_async_call_sends_it_too(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    asyncio.run(_gateway(monkeypatch, seen).acomplete(HI, model=OR_MODEL, thinking=False))
    assert seen[-1]["extra_body"] == OFF


def test_a_call_that_does_not_ask_or_asks_for_thinking_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen)
    gateway.complete(HI, model=OR_MODEL)
    gateway.complete(HI, model=OR_MODEL, thinking=True)
    assert all("extra_body" not in call for call in seen)


def test_the_switch_stays_an_openrouter_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete(HI, model="openai/gpt-4.1-mini", thinking=False)
    assert "extra_body" not in seen[-1]


def test_a_fallback_on_another_route_is_not_sent_the_openrouter_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen, CHIMERA_FALLBACK_MODELS="openai/gpt-4.1-mini")
    import litellm

    def failing_primary(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        if kwargs["model"] == OR_MODEL:
            raise RuntimeError("upstream exploded")  # an unknown error falls back
        return _resp()

    monkeypatch.setattr(litellm, "completion", failing_primary)
    gateway.complete(HI, model=OR_MODEL, thinking=False)
    assert [call["model"] for call in seen] == [OR_MODEL, "openai/gpt-4.1-mini"]
    assert seen[0]["extra_body"] == OFF
    assert "extra_body" not in seen[1]


def test_a_callers_extra_body_keeps_the_switch_it_did_not_name(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _gateway(monkeypatch, seen).complete(HI, model=OR_MODEL, thinking=False, extra_body={"provider": PIN})
    assert seen[-1]["extra_body"] == {**OFF, "provider": PIN}


def test_a_callers_extra_body_keeps_the_configured_provider_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen, CHIMERA_PROVIDER_ORDER="DeepSeek")
    gateway.complete(HI, model=OR_MODEL, extra_body={"transforms": []})
    assert seen[-1]["extra_body"] == {
        "provider": {"order": ["DeepSeek"], "allow_fallbacks": False},
        "transforms": [],
    }


def test_the_caller_still_wins_on_a_key_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen, CHIMERA_PROVIDER_ORDER="DeepSeek")
    gateway.complete(HI, model=OR_MODEL, thinking=False, extra_body={"provider": PIN})
    assert seen[-1]["extra_body"] == {**OFF, "provider": PIN}


def test_the_streaming_path_merges_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen)
    import litellm

    def fake_stream(**kwargs: Any) -> list[Any]:
        seen.append(kwargs)
        return []

    monkeypatch.setattr(litellm, "completion", fake_stream)
    gateway.stream_complete(HI, model=OR_MODEL, thinking=False, extra_body={"provider": PIN})
    assert seen[-1]["extra_body"] == {**OFF, "provider": PIN}
