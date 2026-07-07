"""Tests for the error->recovery taxonomy + credential-pool cooldowns (M15-C2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.providers.failover import (
    CredentialPool,
    FailoverReason,
    RecoveryAction,
    action_for,
    classify,
)

# --- classification ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Error code: 401 - invalid api key", FailoverReason.AUTH),
        ("RateLimitError: 429 too many requests", FailoverReason.RATE_LIMIT),
        ("This model's maximum context length is 8192 tokens", FailoverReason.CONTEXT_OVERFLOW),
        ("flagged by the content policy", FailoverReason.CONTENT_POLICY),
        ("mistral/x is not a valid model ID", FailoverReason.MODEL_NOT_FOUND),
        ("Request timed out", FailoverReason.TIMEOUT),
        ("503 service unavailable, overloaded", FailoverReason.OVERLOADED),
        ("some unexpected boom", FailoverReason.UNKNOWN),
    ],
)
def test_classify_maps_messages(message: str, expected: FailoverReason) -> None:
    assert classify(RuntimeError(message)) == expected


def test_classify_by_exception_class_name() -> None:
    class RateLimitError(Exception):
        pass

    assert classify(RateLimitError("slow down")) == FailoverReason.RATE_LIMIT


def test_action_mapping() -> None:
    assert action_for(FailoverReason.RATE_LIMIT) == RecoveryAction.ROTATE_KEY
    assert action_for(FailoverReason.MODEL_NOT_FOUND) == RecoveryAction.FALLBACK_MODEL
    assert action_for(FailoverReason.CONTENT_POLICY) == RecoveryAction.ABORT
    assert action_for(FailoverReason.CONTEXT_OVERFLOW) == RecoveryAction.ABORT


# --- credential pool cooldowns -----------------------------------------------------------


def test_pool_cools_a_penalized_key_then_recovers() -> None:
    now = [1000.0]
    pool = CredentialPool(clock=lambda: now[0])
    keys = ["k1", "k2"]
    assert pool.available(keys) == ["k1", "k2"]

    ttl = pool.penalize("k1", FailoverReason.RATE_LIMIT)  # 60s cooldown
    assert ttl == 60.0
    assert pool.is_cooling("k1") is True
    assert pool.available(keys) == ["k2"]  # k1 skipped while cooling

    now[0] += 61.0  # cooldown elapsed
    assert pool.is_cooling("k1") is False
    assert pool.available(keys) == ["k1", "k2"]


def test_pool_reset_clears_cooldown() -> None:
    now = [0.0]
    pool = CredentialPool(clock=lambda: now[0])
    pool.penalize("k", FailoverReason.AUTH)
    assert pool.is_cooling("k")
    pool.reset("k")
    assert pool.is_cooling("k") is False


def test_auth_cools_longer_than_rate_limit() -> None:
    pool = CredentialPool(clock=lambda: 0.0)
    assert pool.penalize("a", FailoverReason.AUTH) > pool.penalize("b", FailoverReason.RATE_LIMIT)


# --- gateway integration: abort short-circuits the fallback chain -------------------------


def _resp(content: str) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, model="m")


def test_abort_reason_does_not_try_the_fallback_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "prov/backup")
    get_settings.cache_clear()
    import litellm

    seen: list[str] = []

    def fake(*, model: str, **_: Any) -> SimpleNamespace:
        seen.append(model)
        raise RuntimeError("request flagged by the content policy")  # -> ABORT

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    with pytest.raises(RuntimeError, match="content policy"):
        LLMGateway().complete([Message(role="user", content="hi")], model="prov/primary")
    assert seen == ["prov/primary"]  # aborted — the fallback model was NOT tried


def test_unknown_error_still_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_FALLBACK_MODELS", "prov/backup")
    get_settings.cache_clear()
    import litellm

    seen: list[str] = []

    def fake(*, model: str, **_: Any) -> SimpleNamespace:
        seen.append(model)
        if model == "prov/primary":
            raise RuntimeError("primary down")  # -> UNKNOWN -> rotate (1 key) -> next model
        return _resp("ok-from-backup")

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway
    from chimera.providers.gateway import Message

    result = LLMGateway().complete([Message(role="user", content="hi")], model="prov/primary")
    assert result.content == "ok-from-backup"
    assert seen == ["prov/primary", "prov/backup"]
