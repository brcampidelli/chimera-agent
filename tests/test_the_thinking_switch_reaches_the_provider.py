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


@pytest.mark.parametrize(
    ("primary", "fallback"),
    [
        (OR_MODEL, "openrouter/z-ai/glm-5.3-flash"),
        ("openrouter/z-ai/glm-5.3-flash", OR_MODEL),
    ],
)
def test_design_generation_shapes_only_the_deepseek_candidate(
    monkeypatch: pytest.MonkeyPatch, primary: str, fallback: str,
) -> None:
    import litellm

    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen, CHIMERA_FALLBACK_MODELS=fallback)
    response_format = {"type": "json_object"}

    def provider(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        if kwargs["model"] == primary:
            raise RuntimeError("upstream exploded")
        return _resp()

    monkeypatch.setattr(litellm, "completion", provider)
    result = gateway.complete(
        HI, model=primary, max_tokens=16_000, response_format=response_format, extra_body={"provider": PIN},
        design_generation=True,
    )
    assert result.model == fallback
    assert result.failed_attempts == [(primary, 8_000 if primary == OR_MODEL else 16_000)]
    assert [call["model"] for call in seen] == [primary, fallback]
    assert all("design_generation" not in call for call in seen)
    for call in seen:
        assert call["extra_body"]["provider"] == PIN
        if call["model"] == OR_MODEL:
            assert call["max_tokens"] == 8_000
            assert "response_format" not in call
            assert call["extra_body"]["reasoning"] == OFF["reasoning"]
        else:
            assert call["max_tokens"] == 16_000
            assert call["response_format"] == response_format
            assert "reasoning" not in call["extra_body"]


@pytest.mark.parametrize(
    ("model", "override"),
    [
        (OR_MODEL, "CHIMERA_API_BASE"),
        (OR_MODEL, "OPENROUTER_API_BASE"),
        (OR_MODEL, "caller"),
        ("openrouter/deepseek/deepseek-v4-flash", None),
        ("deepseek/deepseek-chat", None),
    ],
)
def test_design_generation_leaves_other_endpoints_and_slugs_alone(
    monkeypatch: pytest.MonkeyPatch, model: str, override: str | None,
) -> None:
    custom = "https://models.example.invalid/v1"
    settings = {override: custom} if override and override != "caller" else {}
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen, DEEPSEEK_API_KEY="sk-" + "test", **settings)
    request = {"api_base": custom} if override == "caller" else {}
    response_format = {"type": "json_object"}

    gateway.complete(HI, model=model, response_format=response_format, design_generation=True, **request)
    assert seen[-1]["response_format"] == response_format
    assert "design_generation" not in seen[-1]
    assert "reasoning" not in seen[-1].get("extra_body", {})


def test_a_regular_completion_keeps_deepseeks_json_mode_and_reasoning_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, seen)
    response_format = {"type": "json_object"}

    gateway.complete(HI, model=OR_MODEL, response_format=response_format)
    assert seen[-1]["response_format"] == response_format
    assert "reasoning" not in seen[-1].get("extra_body", {})


@pytest.mark.parametrize("path", ["batch", "stream", "stream-to-batch"])
@pytest.mark.parametrize(
    ("thinking", "suffix", "override", "reasoning", "receipt_suffix"),
    [
        (True, "", False, None, ""),
        (False, "#high", False, OFF["reasoning"], ""),
        (None, "#high", False, {"effort": "high"}, "#high"),
        (False, "#high", True, {"effort": "high"}, "#high"),
    ],
)
def test_design_keeps_the_explicit_thinking_choice_and_names_the_sent_effort(
    monkeypatch: pytest.MonkeyPatch, path: str, thinking: bool | None, suffix: str,
    override: bool, reasoning: dict[str, str | bool] | None, receipt_suffix: str,
) -> None:
    import litellm

    from chimera.orchestration.metering import MeteredBackend

    seen: list[dict[str, Any]] = []
    meter = MeteredBackend(_gateway(monkeypatch, seen), label="design")

    def provider(**kwargs: Any) -> Any:
        seen.append(kwargs)
        if not kwargs.get("stream"):
            return _resp()
        if path == "stream-to-batch":
            raise RuntimeError("503 provider overloaded")
        chunk = SimpleNamespace(delta=_resp().choices[0].message, finish_reason="stop")
        return [SimpleNamespace(choices=[chunk], usage=None)]

    monkeypatch.setattr(litellm, "completion", provider)
    complete = meter.complete if path == "batch" else meter.stream_complete
    result = complete(
        HI, model=OR_MODEL + suffix, thinking=thinking, design_generation=True,
        response_format={"type": "json_object"}, extra_body={"reasoning": reasoning} if override else {},
    )
    assert result.content == "ok"
    assert [call.get("stream", False) for call in seen] == {
        "batch": [False], "stream": [True], "stream-to-batch": [True, False],
    }[path]
    for call in seen:
        assert "response_format" not in call
        assert call.get("extra_body", {}).get("reasoning") == reasoning
    assert result.model == meter.last_model == OR_MODEL + receipt_suffix
