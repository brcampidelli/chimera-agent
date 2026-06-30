"""Tests for AI-provider features: custom endpoint, fallback chain, credential pools (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import Settings, get_settings


def _resp(text: str) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def test_fallback_models_split_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "a/b, c/d ,")
    assert Settings().fallback_models == ["a/b", "c/d"]


def test_api_base_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_API_BASE", "http://localhost:11434")
    assert Settings().api_base == "http://localhost:11434"


def test_model_candidates_are_primary_then_deduped_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "primary,backup,backup")
    get_settings.cache_clear()
    from chimera.providers import LLMGateway

    assert LLMGateway()._model_candidates("primary") == ["primary", "backup"]


def test_complete_falls_back_to_next_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "prov/backup")
    get_settings.cache_clear()
    import litellm

    seen: list[str] = []

    def fake(*, model: str, **_: Any) -> SimpleNamespace:
        seen.append(model)
        if model == "prov/primary":
            raise RuntimeError("primary down")
        return _resp("ok-from-backup")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    result = LLMGateway().complete([Message(role="user", content="hi")], model="prov/primary")
    assert result.content == "ok-from-backup"
    assert seen == ["prov/primary", "prov/backup"]


def test_complete_passes_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_API_BASE", "http://localhost:11434")
    get_settings.cache_clear()
    import litellm

    captured: dict[str, Any] = {}

    def fake(*, model: str, **kw: Any) -> SimpleNamespace:
        captured.update(kw)
        return _resp("ok")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    LLMGateway().complete([Message(role="user", content="hi")], model="m")
    assert captured.get("api_base") == "http://localhost:11434"


def test_complete_raises_when_all_candidates_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "b1,b2")
    get_settings.cache_clear()
    import litellm

    def fake(*, model: str, **_: Any) -> SimpleNamespace:
        raise RuntimeError(f"{model} down")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    with pytest.raises(RuntimeError):
        LLMGateway().complete([Message(role="user", content="hi")], model="p")


# --- Credential pools / key rotation -------------------------------------------------


def test_key_pool_prefers_pool_over_single_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "single")
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "k1,k2,k3")
    assert Settings().key_pool("openrouter") == ["k1", "k2", "k3"]
    monkeypatch.delenv("CHIMERA_OPENROUTER_KEYS")
    assert Settings().key_pool("openrouter") == ["single"]


def test_pool_only_provider_counts_as_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_ANTHROPIC_KEYS", "a1,a2")
    settings = Settings()
    assert "anthropic" in settings.configured_providers()
    assert settings.has_any_key() is True


def test_key_rotator_is_round_robin() -> None:
    from chimera.providers.gateway import _KeyRotator

    rotator = _KeyRotator(["k1", "k2", "k3"])
    assert rotator.order() == ["k1", "k2", "k3"]  # call 1 starts at k1
    assert rotator.order() == ["k2", "k3", "k1"]  # call 2 starts at k2
    assert rotator.order() == ["k3", "k1", "k2"]  # call 3 starts at k3
    assert _KeyRotator([]).order() == []


def test_complete_rotates_pool_keys_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "k1,k2")
    get_settings.cache_clear()
    import litellm

    used: list[str | None] = []

    def fake(*, model: str, **kw: Any) -> SimpleNamespace:
        used.append(kw.get("api_key"))
        return _resp("ok")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    gateway = LLMGateway()
    gateway.complete([Message(role="user", content="hi")], model="openrouter/x")
    gateway.complete([Message(role="user", content="hi")], model="openrouter/x")
    assert used == ["k1", "k2"]  # round-robin across calls


def test_complete_fails_over_across_pool_keys_within_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "bad,good")
    get_settings.cache_clear()
    import litellm

    seen: list[str | None] = []

    def fake(*, model: str, **kw: Any) -> SimpleNamespace:
        key = kw.get("api_key")
        seen.append(key)
        if key == "bad":
            raise RuntimeError("rate limited")
        return _resp("ok-from-good")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    result = LLMGateway().complete([Message(role="user", content="hi")], model="openrouter/x")
    assert result.content == "ok-from-good"
    assert seen == ["bad", "good"]  # failed over to the 2nd key, same model


def test_no_pool_passes_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward-compat: with no pool, the gateway lets LiteLLM read the env key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    import litellm

    captured: dict[str, Any] = {}

    def fake(*, model: str, **kw: Any) -> SimpleNamespace:
        captured.update(kw)
        return _resp("ok")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    LLMGateway().complete([Message(role="user", content="hi")], model="openrouter/x")
    assert "api_key" not in captured
