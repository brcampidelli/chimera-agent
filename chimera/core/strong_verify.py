"""Independent strong-model verification — endorse the answer with a *different, stronger* judge.

The research is sharp here: a weak model checking *itself* triggers self-enhancement bias (it
confidently endorses its own wrong answer), and verifying every turn costs more than it's worth.
But a *stronger, independent* verifier that fires only when a turn proved hard pays off — a user
running real tasks said the same ("a strong final-verification pass is usually worth its cost").

So this is gated in the solve loop: it grades the final answer with an independent stronger model
only on attempts that already needed a retry (the observed-difficulty signal Chimera already
trusts for fusion escalation), not on every easy first-pass success. A below-threshold grade
fails the attempt and feeds a revise-back. A verifier error degrades to "pass" — an independent
check can only add a gate, never falsely block a run because the judge call flaked.
"""

from __future__ import annotations

import re

from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("core.strong_verify")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")

_VERIFY_SYSTEM = (
    "You are an independent senior reviewer, stronger than the model that wrote the answer. Given "
    "a task and a candidate answer, judge whether the answer actually and completely solves the "
    "task. Rate it 0 to 10 (10 = fully correct and complete). Be skeptical; do not assume the "
    "answer is right. Reply with ONLY the number."
)


def _parse_grade(text: str) -> float:
    match = _NUM.search(text)
    if not match:
        return 1.0  # unparseable grade -> don't block (fail-open, like the other gates)
    return max(0.0, min(1.0, float(match.group()) / 10.0))


class StrongVerifier:
    """Grades a final answer with an independent stronger model; a low score fails the attempt."""

    def __init__(self, backend: SupportsComplete, model: str | None = None, threshold: float = 0.6) -> None:
        self.backend = backend
        self.model = model
        self.threshold = threshold

    def verify(self, task: str, answer: str) -> tuple[bool, float]:
        """Return (meets_threshold, score in [0,1]). Degrades to (True, 1.0) on any error."""
        try:
            result = self.backend.complete(
                [
                    Message(role="system", content=_VERIFY_SYSTEM),
                    Message(role="user", content=f"Task:\n{task}\n\nCandidate answer:\n{answer}"),
                ],
                model=self.model,
                temperature=0.0,
            )
            score = _parse_grade(result.content)
            return (score >= self.threshold, score)
        except Exception as exc:  # noqa: BLE001 — a flaky judge must not fail the run
            _log.warning("strong verifier failed, treating as pass: %s", exc)
            return (True, 1.0)
