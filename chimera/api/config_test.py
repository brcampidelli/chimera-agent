"""The ONE honest "does this key actually work?" path for the desktop onboarding wizard.

Everything else in the config surface only checks key PRESENCE
(:func:`chimera.config.Settings.has_any_key` / ``configured_providers`` / the ``doctor`` snapshot):
none of them authenticate. This module is the sole place that makes a REAL, minimal model call, so
the wizard can truthfully say "verified — it works" only after this succeeds, never fabricated.

On any failure (no key, bad key, network, timeout) it returns ``ok=False`` with a short, secret-free
message — a key or a full stack trace is NEVER leaked to the client.
"""

from __future__ import annotations

from typing import Any

from chimera.config import get_settings
from chimera.providers.gateway import Message, MissingCredentialsError


def _short(error: str, *, limit: int = 200) -> str:
    """Collapse a provider error to one short line (never a multi-line stack trace)."""
    return " ".join(error.split())[:limit]


def test_provider(model: str | None = None) -> dict[str, Any]:
    """Make one MINIMAL real completion to prove a provider key authenticates.

    Returns ``{ok, model, error}``. ``ok`` is True ONLY when a live 1-token call succeeded — this is
    the app's sole "key works" signal (the presence checks never authenticate). The completion cache
    is bypassed so a test is always a fresh call, not a stale $0 cache hit that could mask a revoked
    key. Any failure is caught and returned as ``ok=False`` with a short, secret-free message.
    """
    from chimera.providers import LLMGateway

    settings = get_settings()
    resolved = model or settings.default_model
    try:
        gateway = LLMGateway(settings)
        gateway.cache = None  # a test MUST be a real call — never served from the completion cache
        result = gateway.complete(
            [Message(role="user", content="ping")],
            model=resolved,
            temperature=0,
            max_tokens=1,
            timeout=20,  # a hung provider must fail the test in ~20s, not block the request
        )
    except MissingCredentialsError:
        return {"ok": False, "model": resolved, "error": "No provider key configured."}
    except Exception as exc:  # noqa: BLE001 — any provider/network error is a failed test, not a crash
        return {"ok": False, "model": resolved, "error": _short(str(exc))}
    return {"ok": True, "model": result.model or resolved, "error": None}
