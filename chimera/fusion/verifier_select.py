"""Verifier-based sample selection (Weaver-lite) — pick the best of N, don't just vote.

Self-consistency selects among N samples by majority *agreement*, which caps out at what the
weak generator agrees with — often the wrong answer, agreed on. The research lever is
*verification*: score each candidate with a (cheap) verifier and take the top one. Weaver showed
that aggregating even *weak* verifiers lifts a weak generator toward strong-model accuracy, and
the ensemble distills into a tiny cross-encoder — so the verifier can be cheap and still select
well.

This is the injectable core: a ``Scorer`` scores one ``(task, answer)`` in [0, 1];
:class:`VerifierSelector` runs an ensemble of scorers over the candidates and returns the best.
The scorers are injected, so selection is unit-tested with fakes; :func:`llm_scorer` is the real
LLM-judge scorer built on the gateway. A scorer that errors is skipped (never breaks selection).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("fusion.verifier_select")

# Scores how well an answer solves a task, in [0, 1] (higher = better).
Scorer = Callable[[str, str], float]

_SCORE_SYSTEM = (
    "You are a strict grader. Given a task and a candidate answer, rate how well the answer solves "
    "the task on an integer scale from 0 to 10 (10 = fully correct and complete). Judge only the "
    "answer's correctness for the task. Reply with ONLY the number."
)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Selection:
    """The chosen candidate: its index, the candidate text, and its mean score."""

    index: int
    answer: str
    score: float


class VerifierSelector:
    """Selects the best of N candidate answers by aggregating an ensemble of scorers."""

    def __init__(self, scorers: list[Scorer]) -> None:
        if not scorers:
            raise ValueError("VerifierSelector needs at least one scorer")
        self.scorers = scorers

    def _mean_score(self, task: str, answer: str) -> float:
        values: list[float] = []
        for scorer in self.scorers:
            try:
                values.append(float(scorer(task, answer)))
            except Exception as exc:  # noqa: BLE001 — a broken scorer is skipped, not fatal
                _log.debug("scorer failed on a candidate, skipping it: %s", exc)
        return sum(values) / len(values) if values else 0.0

    def select(self, task: str, candidates: list[str]) -> Selection:
        """Return the highest-scoring candidate (ties broken by original order, stable)."""
        if not candidates:
            raise ValueError("no candidates to select from")
        best = Selection(0, candidates[0], -1.0)
        for i, candidate in enumerate(candidates):
            score = self._mean_score(task, candidate)
            if score > best.score:
                best = Selection(i, candidate, score)
        return best


def _parse_score(text: str) -> float:
    """Parse a 0-10 grade into a [0, 1] score; 0.0 if unparseable."""
    match = _NUM.search(text)
    if not match:
        return 0.0
    return max(0.0, min(1.0, float(match.group()) / 10.0))


def llm_scorer(backend: SupportsComplete, model: str | None = None) -> Scorer:
    """A verifier that asks a model to grade an answer 0-10 (normalized to [0, 1])."""

    def score(task: str, answer: str) -> float:
        result = backend.complete(
            [
                Message(role="system", content=_SCORE_SYSTEM),
                Message(role="user", content=f"Task:\n{task}\n\nCandidate answer:\n{answer}"),
            ],
            model=model,
            temperature=0.0,
        )
        return _parse_score(result.content)

    return score
