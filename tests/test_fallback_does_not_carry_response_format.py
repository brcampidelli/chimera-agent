"""A caller's response_format reaches the primary's provider, not a fallback on another provider.

The blocking gateway call sends every keyword through to LiteLLM and sets no drop_params. A fallback
on a provider that does not take ``response_format`` would fail outright, where the primary would
have answered, so the parameter stays with the primary's provider. A fallback on the same provider
still receives it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings


def _resp(text: str) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _run(monkeypatch: pytest.MonkeyPatch, fallbacks: str, primary: str) -> dict[str, dict[str, Any]]:
    """Make the primary fail, let the fallback answer, and return the keywords each model was sent."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", fallbacks)
    get_settings.cache_clear()
    import litellm

    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    sent: dict[str, dict[str, Any]] = {}

    def fake(*, model: str, **kwargs: Any) -> SimpleNamespace:
        sent[model] = kwargs
        if model == primary:
            raise RuntimeError("primary down")
        return _resp("answered")

    monkeypatch.setattr(litellm, "completion", fake)
    result = LLMGateway().complete(
        [Message(role="user", content="hi")], model=primary,
        response_format={"type": "json_object"},
    )
    assert result.content == "answered"
    return sent


def test_a_fallback_on_another_provider_is_not_sent_the_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _run(monkeypatch, fallbacks="other/backup", primary="prov/primary")

    assert sent["prov/primary"]["response_format"] == {"type": "json_object"}
    assert "response_format" not in sent["other/backup"]


def test_a_fallback_on_the_same_provider_keeps_the_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _run(monkeypatch, fallbacks="prov/backup", primary="prov/primary")

    assert sent["prov/backup"]["response_format"] == {"type": "json_object"}
