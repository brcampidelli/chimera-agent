"""Cost-aware routing between a single model and the fusion engine.

Fusion is 2-3x more expensive, so it is *selective*. The router sends tool-calling
turns to a single model (fusion does not tool-call) and reserves fusion for turns worth
the cost: long or deep-reasoning ones (length + intent keywords), **and short but
error-sensitive ones** — exact computations/transformations where a single wrong token
ruins the answer (arithmetic, counting, digit ops). Those short tasks used to slip
through the length/keyword gate and route to a single model, which is exactly where a
lone model's slip corrupts a long chain; fusing them closes that hole. Forced via mode.

The router prices a turn *up front*, but a task that looked easy can turn out hard. An
optional ``escalate_on_fail`` verifier closes that gap: when a check on the single-model
result fails, the turn re-escalates to fusion instead of being accepted — difficulty read
from the review surface, not only predicted (issue #3). Opt-in; no verifier = unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from chimera.providers.gateway import CompletionResult, Message, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("fusion.router")

Mode = Literal["auto", "always", "never"]

# A check on a single-model result: True = acceptable, False = re-escalate this turn to fusion.
EscalationVerifier = Callable[[CompletionResult], bool]

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
# The hyphen (subtraction) is required to be whitespace-padded ("7 - 3"): a TIGHT digit-hyphen-digit
# is far more often a date ("2026-07-10"), a range ("10-20"), or a version than a subtraction, and
# routing those to fusion would betray the "cost-aware" promise. The other operators stay tight.
_ARITH_EXPR = re.compile(r"\d\s*[+*/×÷]\s*\d|\d\s+-\s+\d|\d\s*%")

# Why a turn was (not) routed to fusion — useful for cost auditing and telemetry.
FuseReason = Literal["mode", "length", "keyword", "precision", "arithmetic", "none"]


def _sum_tokens(results: list[CompletionResult], field_name: str) -> int | None:
    """Sum a token field across sampled results (None if none reported)."""
    values = [getattr(r, field_name) for r in results if getattr(r, field_name) is not None]
    return sum(values) if values else None


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
        *,
        escalate_on_fail: EscalationVerifier | None = None,
        agreement_k: int = 1,
        agreement_threshold: float = 0.85,
        agreement_temperature: float = 0.7,
    ) -> None:
        self.single = single
        self.fusion = fusion
        self.policy = policy or RoutingPolicy()
        # Optional: re-escalate a single-model turn to fusion when its result fails this check.
        self.escalate_on_fail = escalate_on_fail
        # Agreement-based escalation (opt-in): sample K cheap answers; if they DISAGREE that's a
        # free "this turn is uncertain/hard" signal (no logprobs, beats verbalized confidence), so
        # escalate to fusion. If they agree, take the consensus cheaply. K<=1 disables it.
        self.agreement_k = max(1, agreement_k)
        self.agreement_threshold = agreement_threshold
        self.agreement_temperature = agreement_temperature

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
        # Agreement escalation: a turn priced as single but where cheap samples disagree is hard.
        if self.agreement_k > 1:
            result = self._agree_or_escalate(messages, model, max_tokens)
        else:
            result = self.single.complete(
                messages, model=model, temperature=temperature, max_tokens=max_tokens
            )
        # Observed difficulty (issue #3): a turn priced as single may still be hard. If a
        # check on its result fails, re-escalate this turn to fusion rather than accept it.
        # Guard: if agreement escalation ALREADY produced a fusion result, don't fuse a second,
        # redundant time — that would silently double the cost of the "cost-aware" router.
        if (
            self.escalate_on_fail is not None
            and result.model != "fusion"
            and not self.escalate_on_fail(result)
        ):
            _log.debug("single result failed verification; re-escalating turn to fusion")
            return self.fusion.complete(messages, temperature=temperature)
        return result

    def _agree_or_escalate(
        self, messages: list[MessageLike], model: str | None, max_tokens: int | None
    ) -> CompletionResult:
        """Sample K cheap answers; return the consensus, or escalate to fusion on disagreement."""
        from chimera.fusion.consistency import majority

        samples = [
            self.single.complete(
                messages, model=model, temperature=self.agreement_temperature, max_tokens=max_tokens
            )
            for _ in range(self.agreement_k)
        ]
        winner = majority([s.content for s in samples], threshold=self.agreement_threshold)
        if winner is None:
            _log.debug("low agreement over %d samples; escalating turn to fusion", self.agreement_k)
            return self.fusion.complete(messages, temperature=0.3)
        return CompletionResult(
            content=winner,
            model="agreement",
            prompt_tokens=_sum_tokens(samples, "prompt_tokens"),
            completion_tokens=_sum_tokens(samples, "completion_tokens"),
        )
