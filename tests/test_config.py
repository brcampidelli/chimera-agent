"""Tests for configuration parsing."""

from __future__ import annotations

import pytest

from chimera.config import Settings


def test_fusion_panel_splits_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_FUSION_PANEL", "prov/a, prov/b ,prov/c")
    settings = Settings(_env_file=None)
    assert settings.fusion_panel == ["prov/a", "prov/b", "prov/c"]


def test_configured_providers_reflects_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    settings = Settings(_env_file=None)
    assert settings.configured_providers() == ["openai"]
    assert settings.has_any_key() is True


def test_no_keys_means_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.has_any_key() is False
