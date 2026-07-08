"""Harness-enforced token budgets for delegations (M16-A4).

The evidence rule: effort scaling must live in the HARNESS, not the prompt —
over-delegation and runaway loops are documented failure modes, and a model
asked nicely to "be brief" is not a control mechanism. :class:`BudgetedBackend`
wraps any ``SupportsComplete`` and enforces a hard ceiling: pre-call it refuses
(or soft-notifies) when the budget is spent and clamps ``max_tokens`` to the
remainder; post-call it consumes what the provider reports.

Honesty: when a provider reports no usage (free tiers often don't), the
chars/4 fallback is used and the budget is flagged ``estimated`` — the flag
propagates into receipts so estimated numbers never masquerade as measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from chimera.orchestration.receipts import estimate_tokens
from chimera.providers.gateway import CompletionResult, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("orchestration.budget")


class BudgetExceeded(RuntimeError):
    """Raised (hard mode) when a call would exceed the delegation's token budget."""


class TokenBudget:
    """A mutable token allowance for one delegation.

    ``consume`` prefers provider-reported usage; the chars/4 fallback flips
    :attr:`estimated` permanently (one estimated row taints the total — that is
    the point: you can no longer present it as fully measured).
    """

    def __init__(self, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens
        self._spent = 0
        self._estimated = False
        self._cache_read = 0
        self._cache_write = 0

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def cache_read(self) -> int:
        """Prompt-cache HIT tokens accumulated across this delegation (billed cheap)."""
        return self._cache_read

    @property
    def cache_write(self) -> int:
        """Prompt-cache WRITE tokens accumulated across this delegation."""
        return self._cache_write

    def note_cache(self, read: int | None, write: int | None) -> None:
        """Record provider-reported cache tokens (subset of the prompt tokens already
        counted in ``spent`` — kept separately so receipts can price the real dollars)."""
        self._cache_read += read or 0
        self._cache_write += write or 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._spent)

    @property
    def exhausted(self) -> bool:
        return self._spent >= self.max_tokens

    @property
    def estimated(self) -> bool:
        """True if ANY consumption relied on the chars/4 fallback."""
        return self._estimated

    def consume(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        *,
        text_fallback: str = "",
    ) -> None:
        """Record spend. If EITHER side of provider usage is missing, the count can't
        be trusted as measured -> fall back to chars/4 on ``text_fallback`` and flag
        estimated (a partial ``prompt=1200, completion=None`` must not pass as exact)."""
        if prompt_tokens is None or completion_tokens is None:
            self._spent += estimate_tokens(text_fallback)
            self._estimated = True
        else:
            self._spent += prompt_tokens + completion_tokens


class BudgetedBackend:
    """A ``SupportsComplete`` that enforces a :class:`TokenBudget` around another backend.

    Modes:
    - ``hard`` (default): a call at/over budget raises :class:`BudgetExceeded`.
    - ``soft``: returns a truncation-notice result instead of calling the model —
      the caller sees an explicit "[budget exhausted]" answer, never a silent cut.
    - ``count_only``: never blocks; just meters. Used to instrument a BASELINE arm
      symmetrically in A/B benches so token accounting is identical in both arms.
    """

    def __init__(
        self,
        inner: SupportsComplete,
        budget: TokenBudget,
        *,
        mode: Literal["hard", "soft", "count_only"] = "hard",
    ) -> None:
        self.inner = inner
        self.budget = budget
        self.mode = mode

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        if self.mode != "count_only" and self.budget.exhausted:
            if self.mode == "hard":
                raise BudgetExceeded(
                    f"delegation budget exhausted: {self.budget.spent}/{self.budget.max_tokens} tokens"
                )
            _log.warning("budget exhausted (soft): returning truncation notice")
            return CompletionResult(
                content=(
                    "[budget exhausted] This delegation hit its token budget "
                    f"({self.budget.max_tokens} tokens). Summarize what you have."
                ),
                model=model or "budget",
            )

        effective_max = max_tokens
        if self.mode != "count_only":
            # Clamp the response size to what's left so the last call can't blow past
            # the ceiling by more than the prompt side.
            remaining = self.budget.remaining
            effective_max = min(max_tokens, remaining) if max_tokens else remaining

        result = self.inner.complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=effective_max,
            tools=tools,
            **kwargs,
        )
        prompt_text = "\n".join(str(m) for m in messages)
        self.budget.consume(
            result.prompt_tokens,
            result.completion_tokens,
            text_fallback=prompt_text + (result.content or ""),
        )
        self.budget.note_cache(result.cache_read_tokens, result.cache_write_tokens)
        return result


@dataclass(frozen=True)
class EffortPolicy:
    """Harness-enforced effort scaling: how many workers / how big a budget per shape.

    Anthropic's documented failure mode is the lead agent spawning many subagents
    for trivial asks; these numbers cap that in code, not in prose.
    """

    simple_budget: int = 3_000
    complex_budget: int = 8_000
    max_parallel_workers: int = 4

    def workers_for(self, shape: str, subtask_count: int) -> int:
        """How many workers a task shape may actually get (never more than asked)."""
        if shape == "simple":
            return min(1, subtask_count)
        return max(1, min(subtask_count, self.max_parallel_workers))

    def budget_for(self, shape: str) -> int:
        """Per-delegation token budget for a task shape."""
        return self.simple_budget if shape == "simple" else self.complex_budget
