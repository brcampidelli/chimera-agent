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

from chimera.fusion.engine import FusionFailed, _normalize_ws
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


def vote_text(result: CompletionResult) -> str:
    """What one sample contributes to a vote: its text, or ``""`` when it was cut off.

    A reply that stopped at the output ceiling (``finish_reason == "length"``) did not answer, and
    two replies cut off at the same point can be word-for-word identical — a reasoning model that
    spends the budget on the same opening does exactly that. Mapped to ``""`` it still counts in
    the denominator of :func:`majority` (it was asked) but can never be part of the winning cluster.
    """
    if str(getattr(result, "finish_reason", "") or "") == "length":
        return ""
    return result.content or ""


def _cluster(answers: list[str], threshold: float) -> list[list[int]]:
    """Greedily group answer indices by text similarity (>= ``threshold`` to a cluster head).

    An empty answer joins no cluster and heads none. ``SequenceMatcher("", "").ratio()`` is 1.0,
    so without this two blank replies were a perfect match and formed a "majority" of nothing.
    """
    norms = [_normalize_ws(a) for a in answers]
    clusters: list[list[int]] = []
    for i, norm in enumerate(norms):
        if not norm:
            continue
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
    Empty answers count toward "the samples" but never toward a cluster, so they can only make a
    majority harder to reach — pass truncated replies through :func:`vote_text` for the same reason.
    """
    if not answers:
        return None
    clusters = _cluster(answers, threshold)
    if not clusters:
        return None  # every answer was empty: there is nothing to agree on
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
        votes = [vote_text(s) for s in samples]
        # Verifier selection (Weaver-lite): pick the best-scored candidate rather than the most
        # agreed-on one — verification lifts a weak generator past what it merely agrees with.
        if self.selector is not None:
            chosen_answer = self.selector.select(_last_user_text(messages), answers).answer
            return self._result(chosen_answer, samples)
        winner = majority(votes, threshold=self.threshold)
        if winner is not None:
            return self._result(winner, samples)
        if not any(v.strip() for v in votes):
            return self._unvoted(samples)
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

    def _unvoted(self, samples: list[CompletionResult]) -> CompletionResult:
        """No sample is a usable vote: say so instead of synthesising from nothing.

        The synthesiser used to be handed ``Candidate 1:`` followed by blanks and its reply shipped as
        ``model="self-consistency"`` — one model answering alone, labelled as an aggregate of N, the
        case S30-02 made a declared failure in fusion. With no text at all that is
        :class:`FusionFailed`. With text that was only ever cut off, the first such sample comes back
        as itself: its own model and ``finish_reason``, the tokens of all N, and
        ``route_meta["aggregation"] == "none"`` so nobody reads it as a consensus.
        """
        cut = next((s for s in samples if (s.content or "").strip()), None)
        if cut is None:
            reasons = ", ".join(str(getattr(s, "finish_reason", "") or "none reported") for s in samples)
            raise FusionFailed(
                f"no self-consistency sample produced an answer ({len(samples)} empty; finish_reason: {reasons})"
            )
        _log.warning("self-consistency: all %d samples were cut off; returning one unvoted", self.n)
        merged = self._result(cut.content, samples)
        return cut.model_copy(
            update={
                "prompt_tokens": merged.prompt_tokens,
                "completion_tokens": merged.completion_tokens,
                "route_meta": {
                    **(cut.route_meta or {}),
                    "kind": "self-consistency",
                    "aggregation": "none",
                    "reason": "every sample was cut off at the output ceiling",
                },
            }
        )

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
