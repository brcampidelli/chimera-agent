"""Tests for AI-provider features: custom endpoint + fallback chain (no network)."""

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
