"""A probability over a closed set of labels, read off the first token a model wrote.

The reading the calibration literature recommends for a decision with fixed options, and the one
`bench/PLAN-study20-calibrated-decisions.md` §1.3 keeps: the model answers **decision-first** — the
label is the first thing it writes, no reasoning before it — and the probability is the mass its
first token put on each label, **renormalized over the labels** (arXiv 2603.06604 eq. 1: the share
of the valid-label mass, so a model that was about to write a sentence and merely had ALLOW as its
likeliest first word is not read as "0.9 ALLOW"). The unnormalized mass is returned beside the
shares for that reason: a low ``mass`` says the model was not choosing among the labels at all.

What this does NOT do, on purpose: read a token that comes *after* a reasoning trace. Measured in
arXiv 2601.13284 (Findings of ACL 2026): swap the trace for one of the opposite label and the decision
token follows it in 92–100% of cases while its probability stays at ~1; under RL with a verifiable
reward the AUROC of that probability against correctness reached 56 (chance is 50). A decision token
after ``</think>`` extracts the trace; it does not decide. Callers that want a number from a reasoning
model turn the reasoning off for the call (`LLMGateway.complete(..., thinking=False)`) or ask for a
verbalized probability instead — a separate signal with its own bench.

Returns ``None`` when the result carries no logprobs, which is what a route that ignored the request
looks like (:attr:`chimera.providers.gateway.CompletionResult.logprobs` says why that is common), and
what a reasoning route looks like. ``None`` is *no signal*, never 0.5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LabelProbabilities:
    """Shares over the labels, and how much of the first token's mass was on any label."""

    shares: dict[str, float]
    """Each label's share of the label mass; sums to 1 when ``mass`` > 0."""
    mass: float
    """The unnormalized probability the first token put on the labels together, in [0, 1]. Low mass
    means the model was not choosing among these labels — read ``shares`` with that in mind."""
    first_token: str
    """The token the model actually wrote first, verbatim."""
    matched: dict[str, list[str]] = field(default_factory=dict)
    """Which top-logprob tokens counted for each label, for the reader who wants to check."""

    @property
    def top(self) -> str:
        return max(self.shares, key=lambda k: self.shares[k]) if self.shares else ""


def _canon(text: str) -> str:
    return text.strip().strip("\"'`*_").casefold()


def label_probabilities(
    result: Any, labels: list[str] | tuple[str, ...], *, position: int = 0
) -> LabelProbabilities | None:
    """Read the token at ``position`` (default: the first generated token) against ``labels``.

    A top-logprob token counts for a label when, after trimming whitespace and quotes and folding
    case, it equals the label or is a non-empty **prefix** of it that is a prefix of no other label
    (``RE`` for ``REVIEW`` counts; ``A`` for ``ALLOW`` counts only if no other label starts with
    ``A``). The token the model wrote is included even when the route listed no alternatives, so a
    route that returns ``logprob`` without ``top_logprobs`` still yields a one-label reading with its
    own mass. Duplicate tokens in a route's list (some send ``" ALLOW"`` and ``"ALLOW"`` both) each
    add their mass — that is what the model would have accepted as that label.
    """
    entries = getattr(result, "logprobs", None)
    if not entries or position < 0 or position >= len(entries):
        return None
    entry = entries[position]
    canon_labels = {label: _canon(label) for label in labels}
    if len(set(canon_labels.values())) != len(labels) or any(not v for v in canon_labels.values()):
        raise ValueError("labels must be distinct and non-empty after normalisation")

    def label_of(token: str) -> str | None:
        t = _canon(token)
        if not t:
            return None
        hits = [label for label, c in canon_labels.items() if c == t or c.startswith(t)]
        return hits[0] if len(hits) == 1 else None

    seen: dict[str, float] = {}
    candidates: list[dict[str, Any]] = list(entry.get("top_logprobs") or [])
    written = {"token": entry.get("token", ""), "logprob": entry.get("logprob", -math.inf)}
    if all(c.get("token") != written["token"] for c in candidates):
        candidates.append(written)
    for alt in candidates:
        token = str(alt.get("token", ""))
        logprob = alt.get("logprob")
        if not isinstance(logprob, (int, float)):
            continue
        # The same token string twice would double-count; a route that lists it once with and once
        # without a leading space is two tokens, and both are kept.
        key = token
        if key in seen:
            continue
        seen[key] = float(logprob)
    mass_by_label: dict[str, float] = {label: 0.0 for label in labels}
    matched: dict[str, list[str]] = {label: [] for label in labels}
    for token, logprob in seen.items():
        label = label_of(token)
        if label is None:
            continue
        mass_by_label[label] += math.exp(logprob)
        matched[label].append(token)
    mass = sum(mass_by_label.values())
    mass = min(max(mass, 0.0), 1.0)
    if mass <= 0.0:
        shares = {label: 0.0 for label in labels}
    else:
        raw_total = sum(mass_by_label.values())
        shares = {label: v / raw_total for label, v in mass_by_label.items()}
    return LabelProbabilities(
        shares=shares, mass=mass, first_token=str(entry.get("token", "")),
        matched={k: v for k, v in matched.items() if v},
    )
