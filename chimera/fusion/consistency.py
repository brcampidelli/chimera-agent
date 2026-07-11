"""Self-consistency (best-of-N) — cheap fusion when a full panel is overkill.

Full LLM-Fusion runs a *panel of different models*. Self-consistency (Wang et al.) is the
one-model analog: sample the SAME model N times at nonzero temperature, then take the answer the
samples most agree on. It's what lifts a single weak/cheap model on reasoning tasks — diversity
comes from sampling instead of from multiple providers, so it costs N calls to one model rather
than a call to each of several.

``SelfConsistency`` implements :class:`~chimera.providers.gateway.SupportsComplete`, so it drops
into any slot a model backend fits (like ``FusionEngine`` does). Voting clusters the samples by
text similarity; a unique majority cluster wins, and a tie or all-distinct set falls back to a
synthesis call that reconciles the candidates — the same "synthesis beats voting" idea as fusion.
"""

from __future__ import annotations

import difflib
from typing import Any

from chimera.fusion.engine import _normalize_ws
from chimera.providers.gateway import CompletionResult, Message, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("fusion.consistency")

_SYNTH_SYSTEM = (
    "You are a synthesizer. Several independent answers to the same task are given below; they "
    "did not reach a clear majority. Write the single best final answer, resolving their "
    "disagreements. Answer the task directly; do not mention that there were multiple candidates."
)


def _last_user_text(messages: list[MessageLike]) -> str:
    """The last user message's text — the 'task' a verifier scores candidates against."""
    for message in reversed(messages):
        data = message.as_dict() if isinstance(message, Message) else message
        if data.get("role") == "user":
            return str(data.get("content", ""))
    return ""


def _cluster(answers: list[str], threshold: float) -> list[list[int]]:
    """Greedily group answer indices by text similarity (>= ``threshold`` to a cluster head)."""
    norms = [_normalize_ws(a) for a in answers]
    clusters: list[list[int]] = []
    for i, norm in enumerate(norms):
        for cluster in clusters:
            head = norms[cluster[0]]
            if difflib.SequenceMatcher(None, head, norm).ratio() >= threshold:
                cluster.append(i)
                break
        else:
            clusters.append([i])
    return clusters


def majority(answers: list[str], *, threshold: float = 0.85) -> str | None:
    """Return the representative of a true-majority similarity cluster (> half), or None.

    None means no consensus — the caller should synthesize instead of voting. "Majority" is strict:
    the winning cluster must hold **more than half** the samples, so a mere plurality (e.g. 2 of 5,
    the rest scattered) does NOT win — a 40% cluster is weak agreement and synthesis is the honest
    fallback. A strict majority also can't tie, so this subsumes the old distinct/tie guards. The
    representative is the longest member of the winning cluster (usually the most complete phrasing).
    """
    if not answers:
        return None
    clusters = _cluster(answers, threshold)
    top = max(clusters, key=len)
    if len(top) < 2 or len(top) * 2 <= len(answers):
        return None  # no cluster holds a strict majority of the samples
    return max((answers[i] for i in top), key=len)


class SelfConsistency:
    """Best-of-N self-consistency over a single backend (a SupportsComplete drop-in)."""

    def __init__(
        self,
        backend: SupportsComplete,
        *,
        n: int = 5,
        temperature: float = 0.8,
        model: str | None = None,
        threshold: float = 0.85,
        selector: Any = None,
    ) -> None:
        self.backend = backend
        self.n = max(1, n)
        self.temperature = temperature
        self.model = model
        self.threshold = threshold
        # Optional VerifierSelector: when set, pick the best of N by a verifier score instead of
        # by majority agreement (Weaver-lite — verification lifts a weak generator past voting).
        self.selector = selector

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """Sample N answers and return the consensus (or a synthesis on a tie).

        ``tools`` is ignored — self-consistency is a reasoning backend, like fusion. ``n == 1``
        is a plain pass-through so the wrapper is free to leave in place.
        """
        chosen = model or self.model
        if self.n <= 1:
            return self.backend.complete(messages, model=chosen, max_tokens=max_tokens, **kwargs)
        samples = [
            self.backend.complete(messages, model=chosen, temperature=self.temperature, max_tokens=max_tokens, **kwargs)
            for _ in range(self.n)
        ]
        answers = [s.content for s in samples]
        # Verifier selection (Weaver-lite): pick the best-scored candidate rather than the most
        # agreed-on one — verification lifts a weak generator past what it merely agrees with.
        if self.selector is not None:
            chosen_answer = self.selector.select(_last_user_text(messages), answers).answer
            return self._result(chosen_answer, samples)
        winner = majority(answers, threshold=self.threshold)
        if winner is not None:
            return self._result(winner, samples)
        _log.debug("self-consistency: no majority over %d samples; synthesizing", self.n)
        synth = self._synthesize(messages, answers, chosen, max_tokens)
        return self._result(synth.content, [*samples, synth])

    def _synthesize(
        self, messages: list[MessageLike], answers: list[str], model: str | None, max_tokens: int | None
    ) -> CompletionResult:
        candidates = "\n\n".join(f"Candidate {i + 1}:\n{a}" for i, a in enumerate(answers))
        prompt: list[MessageLike] = [
            Message(role="system", content=_SYNTH_SYSTEM),
            Message(role="user", content=f"Candidate answers:\n\n{candidates}"),
        ]
        return self.backend.complete(prompt, model=model, temperature=0.2, max_tokens=max_tokens)

    @staticmethod
    def _result(content: str, samples: list[CompletionResult]) -> CompletionResult:
        def _sum(field: str) -> int | None:
            values = [getattr(s, field) for s in samples if getattr(s, field) is not None]
            return sum(values) if values else None

        return CompletionResult(
            content=content,
            model="self-consistency",
            prompt_tokens=_sum("prompt_tokens"),
            completion_tokens=_sum("completion_tokens"),
        )
