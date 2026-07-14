"""Tests for the honest 'does this key work?' path (POST /api/config/test), no network.

``test_provider`` makes ONE minimal real completion. Here we monkeypatch litellm so no key or network
is needed: a fake completion proves the ok:true path, a raised MissingCredentialsError proves the
ok:false path returns a short, secret-free error (mirrors how the gateway tests stub litellm).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings


def _resp(text: str, model: str) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None, model=model)


def test_config_test_ok_on_successful_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "openrouter/some-model")
    get_settings.cache_clear()
    import litellm

    def fake(**_: Any) -> SimpleNamespace:
        return _resp("p", "openrouter/some-model")  # a 1-token reply

    monkeypatch.setattr(litellm, "completion", fake)

    from chimera.api.config_test import test_provider

    out = test_provider()
    assert out["ok"] is True
    assert out["model"] == "openrouter/some-model"
    assert out["error"] is None


def test_config_test_reports_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")  # passes presence; the CALL raises
    get_settings.cache_clear()
    import litellm

    from chimera.providers.gateway import MissingCredentialsError

    def raiser(**_: Any) -> Any:
        raise MissingCredentialsError("boom")

    monkeypatch.setattr(litellm, "completion", raiser)

    from chimera.api.config_test import test_provider

    out = test_provider("openrouter/some-model")
    assert out["ok"] is False
    assert out["error"]  # a non-empty, secret-free message
    assert out["model"] == "openrouter/some-model"


def test_config_test_error_is_short_and_secret_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    import litellm

    def raiser(**_: Any) -> Any:
        raise RuntimeError("boom\n" + "x" * 500)  # a long, multi-line provider error

    monkeypatch.setattr(litellm, "completion", raiser)

    from chimera.api.config_test import test_provider

    out = test_provider()
    assert out["ok"] is False
    assert out["error"] is not None
    assert "\n" not in out["error"]  # collapsed to one line
    assert len(out["error"]) <= 200  # truncated
