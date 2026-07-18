"""First-class local models: an Ollama model runs keyless, and the local server is wired up."""

from __future__ import annotations

import os
from typing import Any

import pytest

from chimera.providers.gateway import LLMGateway, MissingCredentialsError, _is_local_model


def test_is_local_model_matrix() -> None:
    assert _is_local_model("ollama/llama3") is True
    assert _is_local_model("ollama_chat/qwen2.5") is True
    assert _is_local_model("OLLAMA/Llama3") is True  # case-insensitive
    assert _is_local_model("openrouter/meta-llama/llama-3.1-8b") is False
    assert _is_local_model("gpt-4o") is False
    assert _is_local_model("") is False


def test_local_model_needs_no_key(monkeypatch: Any) -> None:
    gw = LLMGateway()
    # Patch on the class (a pydantic instance may reject attribute shadowing).
    monkeypatch.setattr(type(gw.settings), "has_any_key", lambda _self: False)
    # An Ollama model must pass the credential gate even with zero keys configured.
    gw._require_credentials("ollama/llama3")  # must not raise


def test_non_local_model_still_requires_a_key(monkeypatch: Any) -> None:
    gw = LLMGateway()
    monkeypatch.setattr(type(gw.settings), "has_any_key", lambda _self: False)
    with pytest.raises(MissingCredentialsError):
        gw._require_credentials("openrouter/meta-llama/llama-3.1-8b")


def test_ollama_base_url_is_exported_to_env(monkeypatch: Any) -> None:
    # Building the gateway points LiteLLM's Ollama provider at the configured local server.
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    LLMGateway()  # __init__ exports the base URL
    assert os.environ.get("OLLAMA_API_BASE") == "http://localhost:11434"


def test_existing_ollama_api_base_is_not_overwritten(monkeypatch: Any) -> None:
    monkeypatch.setenv("OLLAMA_API_BASE", "http://192.168.1.50:11434")
    LLMGateway()
    assert os.environ["OLLAMA_API_BASE"] == "http://192.168.1.50:11434"  # user's value wins
