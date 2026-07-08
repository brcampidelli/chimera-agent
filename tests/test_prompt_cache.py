"""M17: explicit prompt-cache breakpoints + cache-token accounting through the budget."""

from __future__ import annotations

from typing import Any

from chimera.orchestration.budget import BudgetedBackend, TokenBudget
from chimera.providers.gateway import CompletionResult, MessageLike
from chimera.providers.prompt_cache import apply_cache_control, needs_explicit_cache_control


def test_anthropic_needs_explicit_others_dont() -> None:
    assert needs_explicit_cache_control("openrouter/anthropic/claude-opus-4-8")
    assert needs_explicit_cache_control("claude-sonnet-5")
    assert not needs_explicit_cache_control("openrouter/deepseek/deepseek-chat-v3.1")
    assert not needs_explicit_cache_control("openrouter/openai/gpt-5.5")


def test_apply_marks_last_system_for_anthropic() -> None:
    msgs = [
        {"role": "system", "content": "stable persona + tools"},
        {"role": "user", "content": "hi"},
    ]
    out = apply_cache_control(msgs, "openrouter/anthropic/claude-opus-4-8")
    block = out[0]["content"]
    assert isinstance(block, list)
    assert block[0]["cache_control"] == {"type": "ephemeral"}
    assert block[0]["text"] == "stable persona + tools"
    assert out[1] == msgs[1]  # user turn untouched


def test_apply_is_noop_for_auto_caching_providers() -> None:
    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
    assert apply_cache_control(msgs, "openrouter/deepseek/deepseek-chat-v3.1") is msgs


def test_apply_noop_when_no_system_message() -> None:
    msgs = [{"role": "user", "content": "y"}]
    assert apply_cache_control(msgs, "claude-sonnet-5") == msgs


class _CacheReportingBackend:
    """Reports cache tokens like a caching provider would on a warm call."""

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
        return CompletionResult(
            content="ok", model=model or "m", prompt_tokens=1_000, completion_tokens=50,
            cache_read_tokens=800, cache_write_tokens=200,
        )


def test_budget_accumulates_cache_tokens() -> None:
    budget = TokenBudget(max_tokens=100_000)
    backend = BudgetedBackend(_CacheReportingBackend(), budget, mode="hard")
    backend.complete([{"role": "user", "content": "go"}])
    backend.complete([{"role": "user", "content": "again"}])
    assert budget.cache_read == 1_600  # 800 * 2
    assert budget.cache_write == 400   # 200 * 2
    # cache tokens are a subset of prompt tokens, already counted in spent.
    assert budget.spent == 2 * 1_050
