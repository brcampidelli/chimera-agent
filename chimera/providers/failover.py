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
from dataclasses import dataclass
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
    NO_CREDIT = "no_credit"  # 402 / the account is out of money
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
    # ROTATE_KEY and not ABORT, deliberately: a key pool can hold keys on DIFFERENT accounts, and
    # one of them may still have balance. Aborting would give up on money that exists. What changes
    # versus the old UNKNOWN path is not the rotation — it is the cooldown below and the message the
    # user finally gets, which says "top up" instead of showing a raw provider exception.
    FailoverReason.NO_CREDIT: RecoveryAction.ROTATE_KEY,
    FailoverReason.UNKNOWN: RecoveryAction.ROTATE_KEY,
}

# Per-credential cooldown (seconds) applied when a key trips a given reason.
_COOLDOWN: dict[FailoverReason, float] = {
    FailoverReason.AUTH: 300.0,  # a bad/revoked key: rest it 5 min
    FailoverReason.RATE_LIMIT: 60.0,  # rate limited: back off 1 min
    FailoverReason.OVERLOADED: 15.0,
    FailoverReason.TIMEOUT: 15.0,
    # Longer than AUTH's five minutes, because the remedies differ in kind: a wrong key is fixed by
    # pasting the right one, while an empty balance is fixed by a payment clearing. Retrying an
    # unfunded key every thirty seconds — what the UNKNOWN path did — is the one certainty here.
    FailoverReason.NO_CREDIT: 900.0,
    FailoverReason.UNKNOWN: 30.0,
}


#: Response headers worth keeping when a provider call fails. These are what a support ticket asks
#: for, and they are the only part of a failed response that is safe to quote back: an identifier the
#: provider minted, not content we sent them.
_TRACE_HEADERS = ("x-request-id", "x-oai-request-id", "cf-ray", "retry-after")


@dataclass(frozen=True)
class ProviderTrace:
    """What a failed provider call left behind that is worth writing down.

    Split from the error message on purpose. The message is prose the provider wrote and may quote
    our prompt back at us; these are identifiers it minted, and they are what turns "my call failed"
    into something a support desk can look up.
    """

    status: int | None = None
    request_id: str | None = None
    ray: str | None = None
    retry_after: str | None = None

    def as_suffix(self) -> str:
        """A short ` [status=429 request_id=… ]` for a log line, or `""` when nothing was found."""
        parts = [
            f"{name}={value}"
            for name, value in (
                ("status", self.status),
                ("request_id", self.request_id),
                ("cf_ray", self.ray),
                ("retry_after", self.retry_after),
            )
            if value is not None
        ]
        return f" [{' '.join(parts)}]" if parts else ""


def status_of(exc: BaseException) -> int | None:
    """The HTTP status behind ``exc``, if it carries one.

    Every LiteLLM error class that wraps a response exposes ``status_code`` or a ``response`` with
    one, and so does ``httpx.HTTPStatusError``. Reading it is what lets :func:`classify` decide from
    a fact instead of from prose — see the note there.
    """
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, bool):  # bools are ints; a True here would read as status 1
            continue
        if isinstance(candidate, int):
            return candidate
    return None


def trace_of(exc: BaseException) -> ProviderTrace:
    """Pull the provider's own identifiers out of a failed call. Never raises."""
    found: dict[str, str] = {}
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        for name in _TRACE_HEADERS:
            try:
                value = headers.get(name)
            except Exception:  # noqa: BLE001 — a header bag that does not behave is not a crash
                continue
            if value:
                found[name] = str(value)[:120]
    return ProviderTrace(
        status=status_of(exc),
        request_id=found.get("x-request-id") or found.get("x-oai-request-id"),
        ray=found.get("cf-ray"),
        retry_after=found.get("retry-after"),
    )


#: Statuses that mean one thing and only one thing. Deliberately partial: 400 is context-overflow on
#: one provider and content-policy on another, and 404 can be a bad model id or a bad route, so those
#: keep going through the message.
_BY_STATUS: dict[int, FailoverReason] = {
    401: FailoverReason.AUTH,
    403: FailoverReason.AUTH,
    402: FailoverReason.NO_CREDIT,
    429: FailoverReason.RATE_LIMIT,
    502: FailoverReason.OVERLOADED,
    503: FailoverReason.OVERLOADED,
    504: FailoverReason.OVERLOADED,
}


def classify(exc: BaseException) -> FailoverReason:
    """Map an exception to a :class:`FailoverReason`.

    **Status first, prose second.** A status code is a fact the protocol defines; the message is
    editorial, and a provider rewrites it without telling anyone. Measured against the real LiteLLM
    exception classes, the prose path holds up better than expected — the class NAME carries most of
    it — but two classes degrade when the wording changes: `ServiceUnavailableError` and
    `InternalServerError` fall to UNKNOWN, and with them the action flips from `fallback_model` to
    `rotate_key`, so a provider outage rotates keys instead of changing model.

    The status path also makes 402 safe to detect. Matching the digits `402` in a message is a trap —
    they turn up in model ids and token counts — which is why the prose branch below asks for the
    words instead, and why the exact answer comes from the status when there is one.
    """
    if (status := status_of(exc)) is not None and status in _BY_STATUS:
        return _BY_STATUS[status]

    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    def any_in(text: str, *needles: str) -> bool:
        return any(n in text for n in needles)

    if any_in(name, "authentication", "permission") or any_in(
        msg, "401", "403", "invalid api key", "no auth"
    ):
        return FailoverReason.AUTH
    # Before RATE_LIMIT on purpose: `insufficient_quota` contains "quota" and would otherwise be
    # read as "wait a minute" when it means "the account is empty". No bare "402" here — the digits
    # appear in model ids and token counts, so the status path above is the only place that reads it.
    if any_in(
        msg,
        "insufficient credit",
        "insufficient_quota",
        "insufficient funds",
        "negative credit",
        "add credits",
        "billing hard limit",
        "exceeded your current quota",
    ):
        return FailoverReason.NO_CREDIT
    if "ratelimit" in name or any_in(msg, "429", "rate limit", "quota", "too many requests"):
        return FailoverReason.RATE_LIMIT
    if "contextwindow" in name or any_in(
        msg, "maximum context", "context length", "too many tokens", "reduce the length"
    ):
        return FailoverReason.CONTEXT_OVERFLOW
    if "contentpolicy" in name or any_in(
        msg, "content policy", "content management policy", "flagged", "safety"
    ):
        return FailoverReason.CONTENT_POLICY
    if "notfound" in name or any_in(
        msg, "not a valid model", "no endpoints", "does not exist", "404", "model_not_found"
    ):
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
