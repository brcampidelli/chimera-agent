"""Tests for harness-enforced token budgets (M16-A4).

The proof test: a runaway fake backend (always emits 2k tokens) provably halts
within a bounded number of calls under a hard budget — enforcement, not
post-facto counting.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.orchestration.budget import (
    BudgetedBackend,
    BudgetExceeded,
    EffortPolicy,
    TokenBudget,
    estimate_tokens,
)
from chimera.providers.gateway import CompletionResult, MessageLike


class RunawayBackend:
    """A backend that never stops: every call reports 2,000 tokens of spend."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_max_tokens: int | None = None

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
        self.calls += 1
        self.last_max_tokens = max_tokens
        return CompletionResult(
            content="more! " * 100, model=model or "runaway",
            prompt_tokens=1_000, completion_tokens=1_000,
        )


class SilentUsageBackend:
    """A backend that reports NO usage (like many free endpoints)."""

    def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content="ok", model="silent")


def test_runaway_backend_provably_halts_under_hard_budget() -> None:
    backend = RunawayBackend()
    budget = TokenBudget(max_tokens=8_000)
    wrapped = BudgetedBackend(backend, budget, mode="hard")
    calls = 0
    with pytest.raises(BudgetExceeded):
        for _ in range(1_000):  # would run 1000 times without enforcement
            wrapped.complete([{"role": "user", "content": "go"}])
            calls += 1
    # 8000 budget / 2000 per call = exactly 4 completed calls, halt on the 5th.
    assert calls == 4
    assert backend.calls == 4  # the 5th call never reached the model
    assert budget.spent == 8_000


def test_soft_mode_returns_notice_instead_of_calling_model() -> None:
    backend = RunawayBackend()
    budget = TokenBudget(max_tokens=2_000)
    wrapped = BudgetedBackend(backend, budget, mode="soft")
    wrapped.complete([{"role": "user", "content": "go"}])  # spends the budget
    notice = wrapped.complete([{"role": "user", "content": "go again"}])
    assert "[budget exhausted]" in notice.content
    assert backend.calls == 1  # second call never hit the model


def test_count_only_mode_never_blocks() -> None:
    backend = RunawayBackend()
    budget = TokenBudget(max_tokens=1_000)
    wrapped = BudgetedBackend(backend, budget, mode="count_only")
    for _ in range(5):
        wrapped.complete([{"role": "user", "content": "go"}])
    assert backend.calls == 5  # metered, not blocked
    assert budget.spent == 10_000
    assert backend.last_max_tokens is None  # no clamping in count_only


def test_max_tokens_clamped_to_remaining() -> None:
    backend = RunawayBackend()
    budget = TokenBudget(max_tokens=3_000)
    wrapped = BudgetedBackend(backend, budget, mode="hard")
    wrapped.complete([{"role": "user", "content": "go"}], max_tokens=50_000)
    assert backend.last_max_tokens == 3_000  # clamped to the full remaining
    wrapped.complete([{"role": "user", "content": "go"}], max_tokens=50_000)
    assert backend.last_max_tokens == 1_000  # 3000 - 2000 spent


def test_missing_usage_falls_back_to_estimate_and_flags() -> None:
    budget = TokenBudget(max_tokens=1_000)
    wrapped = BudgetedBackend(SilentUsageBackend(), budget, mode="hard")
    wrapped.complete([{"role": "user", "content": "x" * 400}])
    assert budget.spent > 0  # chars/4 fallback consumed something
    assert budget.estimated is True  # and it is flagged, permanently
    wrapped.complete([{"role": "user", "content": "y"}])
    assert budget.estimated is True


def test_partial_usage_counts_as_estimated_not_measured() -> None:
    """A provider reporting only one side (prompt=1200, completion=None) must NOT pass
    as exactly measured — it flags estimated and uses the chars/4 fallback."""
    budget = TokenBudget(max_tokens=100_000)
    budget.consume(1_200, None, text_fallback="x" * 800)
    assert budget.estimated is True
    assert budget.spent == estimate_tokens("x" * 800)  # ~200, not the partial 1200


def test_budget_validates_positive() -> None:
    with pytest.raises(ValueError):
        TokenBudget(0)


def test_effort_policy_caps_workers_and_budgets() -> None:
    policy = EffortPolicy(simple_budget=3_000, complex_budget=8_000, max_parallel_workers=4)
    assert policy.workers_for("simple", subtask_count=7) == 1
    assert policy.workers_for("parallel_read", subtask_count=7) == 4  # capped
    assert policy.workers_for("parallel_read", subtask_count=2) == 2  # never more than asked
    assert policy.workers_for("parallel_read", subtask_count=0) == 1  # floor
    assert policy.budget_for("simple") == 3_000
    assert policy.budget_for("parallel_read") == 8_000
