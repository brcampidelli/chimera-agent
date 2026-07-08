"""Explicit prompt-cache breakpoints (M17): make Chimera actually USE caching.

The gateway already *captures* cache tokens (``CompletionResult.cache_read_tokens`` /
``cache_write_tokens``); this module makes caching happen in the first place, so the
token-economy work becomes a continuous *dollar* measurement in the receipts.

Two provider families:
- **Automatic** (OpenAI, DeepSeek, most OpenRouter routes): the provider caches a
  repeated prefix with no request-side marker — we leave the messages untouched.
- **Breakpoint-requiring** (Anthropic / Claude): a stable prefix is only cached if the
  request marks it with ``cache_control: {"type": "ephemeral"}`` on a content block.

We mark the **last system message** — the reliably-stable prefix (tool defs + persona;
for the hierarchy the byte-identical ``WORKER_SYSTEM`` shared across a worker fleet).
Marking is a no-op below the provider's minimum cacheable size, which is fine.

Honest status (measured 2026-07-08): the marker is applied correctly and the gateway
captures cache tokens when a provider reports them (verified on DeepSeek auto-caching:
``cache_read`` went 0 -> 5 on a repeated call). BUT a live probe on
``openrouter/anthropic/claude-haiku-4.5`` did NOT surface cache tokens
(``cache_read=0``, ``cache_write=None`` on both calls) — litellm routes the
``openrouter/`` prefix through its OpenAI-compatible path, which does not appear to
forward Anthropic's content-block ``cache_control`` to the provider. The path that
populates these fields is the **native** ``anthropic/...`` provider (with an
``ANTHROPIC_API_KEY``) or a caching-native OpenRouter route. Shipped opt-in and
correct; the OpenRouter→Anthropic caveat is documented, not hidden.
"""

from __future__ import annotations

from typing import Any

_NEEDS_EXPLICIT = ("anthropic", "claude")


def needs_explicit_cache_control(model: str) -> bool:
    """True for families that require a request-side cache breakpoint (Anthropic)."""
    low = model.lower()
    return any(key in low for key in _NEEDS_EXPLICIT)


def _mark(message: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``message`` with an ephemeral cache breakpoint on its content."""
    content = message.get("content")
    if isinstance(content, str):
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        blocks = [dict(b) for b in content]
        if isinstance(blocks[-1], dict):
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        else:  # pragma: no cover - non-dict block, leave as-is
            return message
    else:
        return message
    return {**message, "content": blocks}


def apply_cache_control(messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """Mark the stable system prefix for caching when the model needs an explicit marker.

    Auto-caching providers get the messages back unchanged (they cache without a marker,
    and an unknown ``cache_control`` field could confuse them). Idempotent-safe: an
    already-marked block just gets the same marker again.
    """
    if not needs_explicit_cache_control(model):
        return messages
    last_sys = max(
        (i for i, m in enumerate(messages) if m.get("role") == "system"),
        default=None,
    )
    if last_sys is None:
        return messages
    return [_mark(m) if i == last_sys else m for i, m in enumerate(messages)]
