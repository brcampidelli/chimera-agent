"""Cost-aware routing between a single model and the fusion engine.

Fusion is 2-3x more expensive, so it is *selective*. The router sends tool-calling
turns to a single model (fusion does not tool-call) and reserves fusion for deep,
high-stakes reasoning turns — detected by length and intent keywords, or forced via
the policy mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from chimera.providers.gateway import CompletionResult, Message, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("fusion.router")

Mode = Literal["auto", "always", "never"]

_DEFAULT_KEYWORDS = (
    "research",
    "compare",
    "analyze",
    "analyse",
    "design",
    "evaluate",
    "trade-off",
    "tradeoff",
    "strategy",
    "pros and cons",
    "in depth",
    "deep dive",
)


def _last_user_text(messages: list[MessageLike]) -> str:
    for message in reversed(messages):
        data = message.as_dict() if isinstance(message, Message) else message
        if data.get("role") == "user":
            return str(data.get("content", ""))
    return ""


@dataclass
class RoutingPolicy:
    """Decides whether a given turn should be fused."""

    mode: Mode = "auto"
    min_chars: int = 280
    keywords: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_KEYWORDS)

    def should_fuse(self, messages: list[MessageLike]) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False
        text = _last_user_text(messages)
        if len(text) >= self.min_chars:
            return True
        low = text.lower()
        return any(keyword in low for keyword in self.keywords)


class RoutedBackend:
    """A :class:`SupportsComplete` that picks single-model vs fusion per call."""

    def __init__(
        self,
        single: SupportsComplete,
        fusion: SupportsComplete,
        policy: RoutingPolicy | None = None,
    ) -> None:
        self.single = single
        self.fusion = fusion
        self.policy = policy or RoutingPolicy()

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
        # Tool-calling turns must go to a single model (fusion doesn't tool-call).
        if tools:
            return self.single.complete(
                messages, model=model, temperature=temperature, max_tokens=max_tokens, tools=tools
            )
        if self.policy.should_fuse(messages):
            _log.debug("routing turn to fusion")
            return self.fusion.complete(messages, temperature=temperature)
        return self.single.complete(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
