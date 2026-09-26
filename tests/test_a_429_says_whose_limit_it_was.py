"""A 429 says whose limit it was: the provider's behind the router, the key's, or that it can't tell.

Every 429 used to become "Every configured provider key is rate-limited … add another key".
Measured on 2026-09-26 through `LLMGateway.complete` on the preset weak rung: the reply was
OpenRouter's "Provider returned error" with ``provider_name: Parasail`` and ``limit_source:
upstream_provider_shared_pool`` — the provider's pool was out, the key had been accepted, and the
sentence sent the person to rotate a key that was fine. The same shape came back from a free route
(Liquid) the same day. The first reply is below verbatim, the second trimmed; the account id is
masked in both.

The router's own limiter on a key is the shape OpenRouter publishes (no provider named,
``X-RateLimit-*`` headers in the metadata). It was not drawn here — 40 free-model calls in a
minute drew only upstream 429s — so that fixture is labelled as published, not measured.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.config import get_settings
from chimera.providers.failover import rate_limit_origin

# Measured 2026-09-26, `LLMGateway.complete` on the preset weak rung at the default ceiling:
# litellm's text for the exception, verbatim but for the masked user_id.
UPSTREAM = (
    "litellm.RateLimitError: RateLimitError: OpenrouterException - "
    '{"error":{"message":"Provider returned error","code":429,'
    '"metadata":{"raw":"mistralai/mistral-small-3.2-24b-instruct is temporarily rate-limited '
    "upstream. Please retry shortly, or add your own key to accumulate your rate limits: "
    'https://openrouter.ai/settings/integrations","provider_name":"Parasail","is_byok":false,'
    '"limit_source":"upstream_provider_shared_pool","remedy_hint":"Retry shortly, add your own '
    "provider key (https://openrouter.ai/settings/integrations), or route to another provider with "
    'provider routing: https://openrouter.ai/docs/features/provider-routing"}},'
    '"user_id":"user_masked"}'
)

# Measured the same day on liquid/lfm-2.5-2.6b:free (raw HTTP), trimmed: one field more, same
# meaning.
UPSTREAM_FREE = (
    'OpenrouterException - {"error":{"message":"Provider returned error","code":429,"metadata":'
    '{"raw":"liquid/lfm-2.5-2.6b:free is temporarily rate-limited upstream. Please retry shortly.",'
    '"provider_name":"Liquid","is_byok":false,"provider_error_code":"rate_limit_exceeded",'
    '"limit_source":"upstream_provider_shared_pool"}},"user_id":"user_masked"}'
)

# OpenRouter's published shape for its own per-key limiter. NOT measured in this repo.
KEY_LIMIT = (
    'OpenrouterException - {"error":{"message":"Rate limit exceeded: free-models-per-min. ",'
    '"code":429,"metadata":{"headers":{"X-RateLimit-Limit":"20","X-RateLimit-Remaining":"0",'
    '"X-RateLimit-Reset":"1758850000000"},"provider_name":null}},"user_id":"user_masked"}'
)

BYOK = UPSTREAM.replace('"is_byok":false', '"is_byok":true')


class _RateLimited(Exception):
    """Shaped like litellm's RateLimitError where the gateway reads it: a 429 status and a text."""

    status_code = 429


# --------------------------------------------------------------------------- reading the reply


def test_the_measured_upstream_reply_reads_as_the_providers_side() -> None:
    origin = rate_limit_origin(_RateLimited(UPSTREAM))
    assert origin.side == "upstream"
    assert origin.provider == "Parasail"
    assert origin.source == "upstream_provider_shared_pool"


def test_the_free_route_reply_reads_the_same() -> None:
    origin = rate_limit_origin(_RateLimited(UPSTREAM_FREE))
    assert (origin.side, origin.provider) == ("upstream", "Liquid")


def test_the_routers_own_limiter_reads_as_the_key() -> None:
    origin = rate_limit_origin(_RateLimited(KEY_LIMIT))
    assert origin.side == "key" and origin.provider is None


def test_a_call_on_the_persons_own_provider_key_is_not_called_upstream() -> None:
    """With BYOK the limit may be on their own key at the provider: "not your key" is a guess."""
    origin = rate_limit_origin(_RateLimited(BYOK))
    assert origin.side == "unknown" and origin.provider == "Parasail"


@pytest.mark.parametrize(
    "text",
    [
        "RateLimitError: 429 too many requests",  # a provider that sends no body the router's way
        'OpenrouterException - {"error":{"message":"Provider ret',  # a body cut short
        "{not json at all",
    ],
)
def test_a_reply_that_does_not_say_is_read_as_not_saying(text: str) -> None:
    assert rate_limit_origin(_RateLimited(text)).side == "unknown"


# --------------------------------------------------------------------------- what the person reads


def _failing_gateway(monkeypatch: pytest.MonkeyPatch, text: str) -> Any:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("CHIMERA_FALLBACK_MODELS", raising=False)
    get_settings.cache_clear()
    import litellm

    def fake(**_: Any) -> Any:
        raise _RateLimited(text)

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway

    return LLMGateway()


def _message(monkeypatch: pytest.MonkeyPatch, text: str) -> str:
    from chimera.providers import CredentialRejectedError
    from chimera.providers.gateway import Message

    gateway = _failing_gateway(monkeypatch, text)
    with pytest.raises(CredentialRejectedError) as excinfo:
        gateway.complete([Message(role="user", content="hi")], model="openrouter/x/y")
    return str(excinfo.value)


def test_a_provider_side_429_says_the_key_was_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _message(monkeypatch, UPSTREAM)
    head = text.split(" Provider said:")[0]
    assert head.startswith("Rate-limited upstream: Parasail")
    assert "not on your key" in head
    assert "upstream_provider_shared_pool" in head
    assert "Every configured provider key" not in text
    assert "OPENROUTER_API_KEY" not in head  # nothing about the key needs changing
    assert "temporarily rate-limited upstream" in text  # the provider's own words are kept


def test_a_key_limit_still_says_it_is_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _message(monkeypatch, KEY_LIMIT)
    assert text.startswith("Every configured provider key is rate-limited.")
    assert "OPENROUTER_API_KEY" in text


def test_a_429_that_does_not_say_whose_is_not_blamed_on_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _message(monkeypatch, "RateLimitError: 429 too many requests")
    assert text.startswith("Rate-limited (429), and the reply does not say whether")
    assert "Every configured provider key" not in text


# --------------------------------------------------------------------------- the key test


def test_the_key_test_does_not_call_a_provider_side_429_a_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wizard's "does this key work?" caught every CredentialRejectedError as a missing key."""
    _failing_gateway(monkeypatch, UPSTREAM)
    from chimera.api.config_test import test_provider

    out = test_provider("openrouter/x/y")
    assert out["ok"] is False
    assert out["error"].startswith("Rate-limited upstream: Parasail")
    assert "\n" not in out["error"] and len(out["error"]) <= 200


def test_the_key_test_still_says_so_when_no_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    import litellm

    from chimera.providers.gateway import MissingCredentialsError

    def raiser(**_: Any) -> Any:
        raise MissingCredentialsError("no key")

    monkeypatch.setattr(litellm, "completion", raiser)
    from chimera.api.config_test import test_provider

    assert test_provider("openrouter/x/y")["error"] == "No provider key configured."
