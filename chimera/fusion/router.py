"""Cost-aware routing between a single model and the fusion engine.

Fusion is 2-3x more expensive, so it is *selective*. The router sends tool-calling
turns to a single model (fusion does not tool-call) and reserves fusion for turns worth
the cost: long or deep-reasoning ones (length + intent keywords), **and short but
error-sensitive ones** — exact computations/transformations where a single wrong token
ruins the answer (arithmetic, counting, digit ops). Those short tasks used to slip
through the length/keyword gate and route to a single model, which is exactly where a
lone model's slip corrupts a long chain; fusing them closes that hole. Forced via mode.
"""

from __future__ import annotations

import re
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

# Short prompts whose answer is a single exact value — one wrong token = wrong answer.
# English + a few PT-BR computation verbs (the operator's day-to-day language).
_PRECISION_KEYWORDS = (
    "compute", "calculate", "how many", "how much", "multiply", "divide", "subtract",
    "sum of", "product of", "reverse", "count the", "number of", "digit", "exactly",
    "round to", "what is the value",
    "calcule", "quantos", "quantas", "quanto é", "multiplique", "divida", "some os",
    "dígitos", "digitos",
)

# An actual arithmetic expression: digit, operator, digit (e.g. "7 * 11", "29×11").
_ARITH_EXPR = re.compile(r"\d\s*[+\-*/×÷]\s*\d")


def _is_error_sensitive(text: str, precision_keywords: tuple[str, ...]) -> bool:
    low = text.lower()
    if any(keyword in low for keyword in precision_keywords):
        return True
    return bool(_ARITH_EXPR.search(text))


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
    precision_keywords: tuple[str, ...] = field(default_factory=lambda: _PRECISION_KEYWORDS)
    fuse_error_sensitive: bool = True

    def should_fuse(self, messages: list[MessageLike]) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False
        text = _last_user_text(messages)
        if len(text) >= self.min_chars:  # long, deep-reasoning turn
            return True
        low = text.lower()
        if any(keyword in low for keyword in self.keywords):  # explicit intent
            return True
        # Short but error-sensitive: an exact-answer task a lone model can quietly botch.
        return self.fuse_error_sensitive and _is_error_sensitive(text, self.precision_keywords)


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
