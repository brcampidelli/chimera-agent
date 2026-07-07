"""Error→recovery taxonomy + a credential pool with TTL cooldowns (M15-C2).

Hermes' production robustness comes from a failure *taxonomy* — each error class maps to a specific
recovery action (rotate the key, fall back to another model, or abort) — plus a credential pool that
marks a key exhausted/dead with a cooldown so a rate-limited or revoked key is skipped for a while
instead of hammered every call. This is the Chimera version, pure and dependency-free (classified by
exception class name + message substrings, so it needs no provider SDK types), injected into the
gateway's existing model×key fallback loop.

The point: a 429 should cool that key and try the next, a bad model id should skip straight to the
fallback model, and a context-overflow or content-policy block should abort — retrying those on
another key just burns calls. Before, every failure was treated identically (try next key, next
model, then give up).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from enum import StrEnum


class FailoverReason(StrEnum):
    """Why a completion attempt failed — the classified error class."""

    AUTH = "auth"  # 401/403 bad or revoked key
    RATE_LIMIT = "rate_limit"  # 429 / quota
    OVERLOADED = "overloaded"  # 502/503 provider overloaded
    CONTEXT_OVERFLOW = "context_overflow"  # prompt too long for the model
    CONTENT_POLICY = "content_policy"  # blocked by the provider's policy
    MODEL_NOT_FOUND = "model_not_found"  # bad/unavailable model id
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    """What to do about a :class:`FailoverReason`."""

    ROTATE_KEY = "rotate_key"  # try the next credential
    FALLBACK_MODEL = "fallback_model"  # skip remaining keys, try the next model
    ABORT = "abort"  # retrying won't help — stop and raise


_ACTION: dict[FailoverReason, RecoveryAction] = {
    FailoverReason.AUTH: RecoveryAction.ROTATE_KEY,
    FailoverReason.RATE_LIMIT: RecoveryAction.ROTATE_KEY,
    FailoverReason.OVERLOADED: RecoveryAction.FALLBACK_MODEL,
    FailoverReason.CONTEXT_OVERFLOW: RecoveryAction.ABORT,
    FailoverReason.CONTENT_POLICY: RecoveryAction.ABORT,
    FailoverReason.MODEL_NOT_FOUND: RecoveryAction.FALLBACK_MODEL,
    FailoverReason.TIMEOUT: RecoveryAction.FALLBACK_MODEL,
    FailoverReason.UNKNOWN: RecoveryAction.ROTATE_KEY,
}

# Per-credential cooldown (seconds) applied when a key trips a given reason.
_COOLDOWN: dict[FailoverReason, float] = {
    FailoverReason.AUTH: 300.0,  # a bad/revoked key: rest it 5 min
    FailoverReason.RATE_LIMIT: 60.0,  # rate limited: back off 1 min
    FailoverReason.OVERLOADED: 15.0,
    FailoverReason.TIMEOUT: 15.0,
    FailoverReason.UNKNOWN: 30.0,
}


def classify(exc: BaseException) -> FailoverReason:
    """Map an exception to a :class:`FailoverReason` by its class name + message (SDK-agnostic)."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    def any_in(text: str, *needles: str) -> bool:
        return any(n in text for n in needles)

    if any_in(name, "authentication", "permission") or any_in(msg, "401", "403", "invalid api key", "no auth"):
        return FailoverReason.AUTH
    if "ratelimit" in name or any_in(msg, "429", "rate limit", "quota", "too many requests"):
        return FailoverReason.RATE_LIMIT
    if "contextwindow" in name or any_in(msg, "maximum context", "context length", "too many tokens", "reduce the length"):
        return FailoverReason.CONTEXT_OVERFLOW
    if "contentpolicy" in name or any_in(msg, "content policy", "content management policy", "flagged", "safety"):
        return FailoverReason.CONTENT_POLICY
    if "notfound" in name or any_in(msg, "not a valid model", "no endpoints", "does not exist", "404", "model_not_found"):
        return FailoverReason.MODEL_NOT_FOUND
    if "timeout" in name or any_in(msg, "timed out", "timeout"):
        return FailoverReason.TIMEOUT
    if any_in(msg, "overloaded", "503", "502", "service unavailable", "bad gateway"):
        return FailoverReason.OVERLOADED
    return FailoverReason.UNKNOWN


def action_for(reason: FailoverReason) -> RecoveryAction:
    """The recovery action for a reason."""
    return _ACTION.get(reason, RecoveryAction.ROTATE_KEY)


class CredentialPool:
    """Tracks per-key cooldowns so an exhausted/dead credential is skipped until it recovers."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}

    def available(self, keys: Iterable[str]) -> list[str]:
        """The keys not currently cooling down, in the given order."""
        now = self._clock()
        return [k for k in keys if self._cooldown_until.get(k, 0.0) <= now]

    def is_cooling(self, key: str) -> bool:
        return self._cooldown_until.get(key, 0.0) > self._clock()

    def penalize(self, key: str, reason: FailoverReason) -> float:
        """Cool ``key`` down for the reason's TTL. Returns the cooldown seconds applied."""
        ttl = _COOLDOWN.get(reason, 30.0)
        self._cooldown_until[key] = self._clock() + ttl
        return ttl

    def reset(self, key: str) -> None:
        """Clear a key's cooldown (e.g., after a success)."""
        self._cooldown_until.pop(key, None)
