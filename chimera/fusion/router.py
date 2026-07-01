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

# Deep-reasoning intent, in the project's main languages (en/pt/es/de/fr/zh/ja).
_DEFAULT_KEYWORDS = (
    # English
    "research", "compare", "analyze", "analyse", "design", "evaluate",
    "trade-off", "tradeoff", "strategy", "pros and cons", "in depth", "deep dive",
    # Portuguese
    "pesquise", "analise", "avalie", "estratégia", "prós e contras", "aprofunde",
    # Spanish
    "investiga", "analiza", "evalúa", "estrategia", "pros y contras", "en profundidad",
    # German
    "recherchiere", "vergleiche", "analysiere", "bewerte", "strategie", "vor- und nachteile",
    # French
    "recherche", "évalue", "stratégie", "avantages et inconvénients", "en profondeur",
    # Chinese
    "研究", "比较", "分析", "评估", "策略", "优缺点", "深入",
    # Japanese
    "調査", "比較", "分析", "評価", "戦略", "長所と短所", "詳細",
)

# Short prompts whose answer is a single exact value — one wrong token = wrong answer.
# Curated to avoid cross-language substring false positives (e.g. no bare "resta"/"some").
_PRECISION_KEYWORDS = (
    # English
    "compute", "calculate", "how many", "how much", "multiply", "divide", "subtract",
    "sum of", "product of", "reverse", "count the", "number of", "digits", "exactly",
    "round to", "what is the value",
    # Portuguese
    "calcule", "calcular", "quantos", "quantas", "quanto é", "multiplique", "divida",
    "soma de", "dígitos", "digitos", "quantidade de", "exatamente", "arredonde", "inverta",
    # Spanish
    "calcula", "cuántos", "cuántas", "cuantos", "cuánto es", "multiplica", "suma de",
    "cantidad de", "exactamente", "redondea",
    # German
    "berechne", "rechne", "wie viele", "wie viel", "multipliziere", "dividiere",
    "summe von", "ziffern", "genau", "runde",
    # French
    "calcule", "calculer", "combien", "multiplie", "divise", "somme de", "chiffres",
    "exactement", "arrondis",
    # Chinese
    "计算", "多少", "相乘", "乘以", "相除", "除以", "位数", "精确", "反转", "求和",
    # Japanese
    "計算", "いくつ", "いくら", "掛け", "割り算", "正確", "反転", "合計",
)

# An actual arithmetic expression: digit, operator, digit (e.g. "7 * 11", "29×11"),
# or a percentage (e.g. "15% of 80", "20% tip") — both are exact numeric answers.
_ARITH_EXPR = re.compile(r"\d\s*[+\-*/×÷]\s*\d|\d\s*%")

# Why a turn was (not) routed to fusion — useful for cost auditing and telemetry.
FuseReason = Literal["mode", "length", "keyword", "precision", "arithmetic", "none"]


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
        return self.fuse_reason(messages) != "none"

    def fuse_reason(self, messages: list[MessageLike]) -> FuseReason:
        """Which gate (if any) routes this turn to fusion — the attribution behind should_fuse."""
        if self.mode == "always":
            return "mode"
        if self.mode == "never":
            return "none"
        text = _last_user_text(messages)
        if len(text) >= self.min_chars:  # long, deep-reasoning turn
            return "length"
        low = text.lower()
        if any(keyword in low for keyword in self.keywords):  # explicit intent
            return "keyword"
        # Short but error-sensitive: an exact-answer task a lone model can quietly botch.
        if self.fuse_error_sensitive:
            if any(keyword in low for keyword in self.precision_keywords):
                return "precision"
            if _ARITH_EXPR.search(text):
                return "arithmetic"
        return "none"


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
