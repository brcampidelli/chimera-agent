"""What the parts that are not the worker actually cost.

A receipt used to price one thing: the worker's own loop, via ``AgentResult.usd``. Everything else
the run pays for — the planner, the manager reviewing each attempt, the requirement checklist, the
strong verifier, the spec-test generator — calls ``backend.complete`` directly and was never priced
anywhere. Not in ``runs.jsonl``, not in ``traces.jsonl``, not in ``usage.jsonl``.

That is not a rounding error; it is a **directional** one, and it points the wrong way. The whole
point of a model-per-role profile is to put a stronger model on planning and review. So the more a
profile spends on exactly what distinguishes it, the more of that spend is invisible — and the
"was it worth it?" panel, whose entire job is to say whether the expensive profile earned its cost,
reported the cheap-looking number with an air of totality.

The fix is a wrapper rather than a new return type on each collaborator. ``Planner.plan`` returns a
``Plan``, ``Manager.review`` returns a verdict; threading a cost out of each would touch every
signature, every caller and every test, and would still miss the next collaborator someone adds.
Metering the *backend* catches every call through it, including ones written after this file.

Deliberately NOT a global counter: an instance per run, handed to the collaborators of that run.
A module-level tally would mix concurrent runs — which is exactly what ``solve-batch`` does — and
produce a number that is wrong in a way nobody would notice, since it would still look plausible.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from chimera.orchestration.receipts import price_delegation
from chimera.telemetry import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.providers.gateway import CompletionResult, MessageLike

_log = get_logger("orchestration.metering")


class MeteredBackend:
    """A ``SupportsComplete`` that records what each call cost, then delegates unchanged.

    Transparent by construction: it forwards every argument and returns the backend's own result
    object untouched. A wrapper that normalised or re-wrapped the result would be a second place for
    behaviour to diverge, and the reason this class exists is that a second place already did.
    """

    def __init__(self, inner: Any, label: str = "") -> None:
        self.inner = inner
        self.label = label
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._usd = 0.0
        self._unpriced = 0
        self._lock = threading.Lock()

    @property
    def usd(self) -> float | None:
        """Total cost, or ``None`` if ANY call's model had an unknown price.

        All-or-nothing, matching ``total_usd`` on the receipt and for the same reason: a partial sum
        is not a conservative estimate, it is a number that is confidently too low — and too low in
        the direction that flatters whichever configuration used an unpriced or free model.

        **No calls is 0.0, not None.** "Nothing was spent" is a fact; ``None`` means "we do not
        know", and the two must not be confused here because this value is added to the worker's.
        Returning ``None`` for an idle meter would poison every attempt of every run that happens
        not to use a planner — turning a fully-known cost into an unknown one, which is the same
        dishonesty as the undercount, just pointing the other way.
        """
        if self._unpriced:
            return None
        return round(self._usd, 6)

    def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
        result: CompletionResult = self.inner.complete(messages, **kwargs)
        self._record(result, kwargs.get("model"))
        return result

    def __getattr__(self, name: str) -> Any:
        """Pass anything else through (``is_isolated``, provider probes, engine internals).

        Only reached for attributes this class does not define, so it cannot shadow the metering.
        Without it, wrapping a backend would silently remove capabilities callers duck-type for.

        ``stream_complete`` is metered here rather than declared as a method, and that placement is
        the point: ``Agent._step`` decides whether to stream with ``hasattr(backend,
        "stream_complete")``. A method on this class would answer True for every backend, so
        wrapping a non-streaming one would make it *look* streamable and then fail at the call.
        Resolving it through ``getattr`` on the inner backend means the wrapper advertises exactly
        what it wraps — and still counts the tokens.
        """
        attr = getattr(self.inner, name)
        if name != "stream_complete":
            return attr

        def metered_stream(messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            result: CompletionResult = attr(messages, **kwargs)
            self._record(result, kwargs.get("model"))
            return result

        return metered_stream

    def _record(self, result: Any, requested_model: str | None) -> None:
        prompt = int(getattr(result, "prompt_tokens", 0) or 0)
        completion = int(getattr(result, "completion_tokens", 0) or 0)
        # The model that ANSWERED, not the one asked for: a fusion panel, a cascade or a failover
        # can answer on a different model than the caller named, and pricing the requested one would
        # invent a number for a call that never happened.
        model = str(getattr(result, "model", "") or requested_model or "")
        usd = price_delegation(model, prompt, completion) if model else None
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            if usd is None:
                self._unpriced += 1
            else:
                self._usd += usd

    def take(self) -> tuple[float | None, int, int]:
        """Read and RESET: the cost since the last read, for attributing per attempt.

        The reset is what makes this usable inside a retry loop — attempt 2's review should be
        attributed to attempt 2, not to the running total. Returns ``(usd, prompt, completion)``.
        """
        with self._lock:
            usd = self.usd
            prompt, completion = self.prompt_tokens, self.completion_tokens
            self.calls = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self._usd = 0.0
            self._unpriced = 0
        return usd, prompt, completion


def add_usd(*values: float | None) -> float | None:
    """Sum costs where ``None`` means *unknown* and poisons the total.

    ``None + 0.02`` is not ``0.02``. Treating it as such is how a receipt reports the cost of the
    legs it happened to price as though it were the cost of the run.
    """
    known = [v for v in values if v is not None]
    if len(known) != len(values):
        return None
    return round(sum(known), 6) if known else None
