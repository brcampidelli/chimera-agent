"""M17: explicit prompt-cache breakpoints + cache-token accounting through the budget."""

from __future__ import annotations

from typing import Any

from chimera.orchestration.budget import BudgetedBackend, TokenBudget
from chimera.providers.gateway import CompletionResult, MessageLike
from chimera.providers.prompt_cache import (
    apply_cache_control,
    apply_rolling_cache_control,
    needs_explicit_cache_control,
)


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


def test_rolling_cache_control_marks_last_content_message() -> None:
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "request"},
        {"role": "assistant", "content": "previous answer"},
    ]

    marked = apply_rolling_cache_control(messages, "anthropic/claude-sonnet")

    assert marked[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert marked[2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert marked[1] == messages[1]


def test_rolling_cache_control_never_exceeds_four_existing_markers() -> None:
    marker = {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}
    messages = [{"role": "user", "content": [dict(marker)]} for _ in range(4)]
    messages.append({"role": "assistant", "content": "tail"})

    marked = apply_rolling_cache_control(messages, "anthropic/claude-sonnet")

    count = sum(
        1
        for message in marked
        for block in (message["content"] if isinstance(message["content"], list) else [])
        if isinstance(block, dict) and "cache_control" in block
    )
    assert count == 4
    assert marked[-1] == messages[-1]


def test_rolling_cache_control_skips_empty_tail_and_marks_tool_content() -> None:
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "assistant", "content": "prior answer"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}]},
    ]

    marked = apply_rolling_cache_control(messages, "anthropic/claude-sonnet")

    assert marked[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert marked[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert marked[2] == messages[2]


def test_rolling_cache_control_marks_tool_result_after_empty_tool_call() -> None:
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "request"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "content": "saida", "tool_call_id": "call-1"},
    ]

    marked = apply_rolling_cache_control(messages, "anthropic/claude-sonnet")

    assert marked[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert marked[1] == messages[1]
    assert marked[2] == messages[2]
    assert marked[3]["content"] == [
        {"type": "text", "text": "saida", "cache_control": {"type": "ephemeral"}}
    ]


def test_prompt_cache_setting_defaults_to_automatic_and_honors_environment(
    monkeypatch: Any,
) -> None:
    from chimera.config import Settings

    monkeypatch.delenv("CHIMERA_PROMPT_CACHE", raising=False)
    assert Settings(_env_file=None).prompt_cache is None
    monkeypatch.setenv("CHIMERA_PROMPT_CACHE", "0")
    assert Settings(_env_file=None).prompt_cache is False


def test_gateway_complete_applies_automatic_rolling_cache_control(
    monkeypatch: Any,
) -> None:
    from types import SimpleNamespace

    import litellm

    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    captured: dict[str, Any] = {}

    def fake_completion(*, messages: list[dict[str, Any]], **_: Any) -> Any:
        captured["messages"] = messages
        choice = SimpleNamespace(
            message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop"
        )
        return SimpleNamespace(choices=[choice], usage=None)

    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    gateway = LLMGateway(Settings(prompt_cache=None, wire_log=False))
    gateway.complete(
        [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}],
        model="openrouter/anthropic/claude-sonnet",
    )

    sent = captured["messages"]
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[1]["content"][0]["cache_control"] == {"type": "ephemeral"}


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
