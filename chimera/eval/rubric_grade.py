"""Authorable rubric grading — turn a free-form answer into a graded, defensible outcome.

The cascade rubric in :mod:`chimera.eval.rubric` is a *fixed* three-dimension gate the Manager uses
inline. This is its complement for evaluation: a **task-authorable** rubric — a list of weighted
criteria supplied as data (per task, per bench) — graded independently into a single weighted score
plus a pass/fail verdict. Some criteria can be marked ``required``: a required criterion that falls
below its gate vetoes the whole outcome no matter how high the weighted score, so "mostly good but
missed the one thing that mattered" reads as a fail, not a pass.

This is the graded-outcome layer the rest of M14 consumes: it produces the richer reward signal RFT
wants, and ``grade_batch`` collapses a set of graded answers into the boolean pass/fail trials the
honest A/B (``chimera.eval.bench_ab``) needs. The per-criterion scorer is injected (reuse
``model_judge`` for a real LLM grader), so grading is fully testable without a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.eval.rubric import JudgeFn, model_judge


@dataclass(frozen=True)
class Criterion:
    """One rubric line: what to check, how much it counts, and whether it is a hard requirement."""

    text: str
    weight: float = 1.0
    required: bool = False  # a required criterion below the gate vetoes the whole outcome

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Criterion:
        return cls(
            text=str(data["text"]),
            weight=float(data.get("weight", 1.0)),
            required=bool(data.get("required", False)),
        )


@dataclass
class Rubric:
    """A weighted set of criteria plus the thresholds that decide pass/fail."""

    criteria: list[Criterion]
    pass_threshold: float = 0.6  # weighted score at/above this passes (absent a required veto)
    required_gate: float = 0.5  # a required criterion below this vetoes the outcome

    @classmethod
    def from_list(cls, rows: list[dict[str, Any]], **kwargs: float) -> Rubric:
        return cls([Criterion.from_dict(r) for r in rows], **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rubric:
        return cls(
            [Criterion.from_dict(r) for r in data.get("criteria", [])],
            pass_threshold=float(data.get("pass_threshold", 0.6)),
            required_gate=float(data.get("required_gate", 0.5)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_threshold": self.pass_threshold,
            "required_gate": self.required_gate,
            "criteria": [
                {"text": c.text, "weight": c.weight, "required": c.required} for c in self.criteria
            ],
        }


@dataclass
class GradedOutcome:
    """The result of grading one answer against a rubric."""

    scores: dict[str, float] = field(default_factory=dict)
    weighted: float = 0.0
    passed: bool = False
    failed_required: list[str] = field(default_factory=list)
    #: Criteria whose scorer could not be read. Kept apart from ``failed_required`` on purpose:
    #: "the grader did not answer" and "the answer missed this" are different facts, and merging
    #: them is what let judge flakiness read as a failing answer.
    unscored: list[str] = field(default_factory=list)
    #: Whether this is a grade at all. False when nothing could be scored, or when a *required*
    #: criterion could not be read — a required criterion is the one the outcome is void without,
    #: so an unreadable one leaves an absence rather than a verdict. ``passed`` is False either
    #: way, but only this field says whether that False means anything.
    determinate: bool = True

    def summary(self) -> dict[str, object]:
        return {
            "weighted": round(self.weighted, 4),
            "passed": self.passed,
            "determinate": self.determinate,
            "failed_required": list(self.failed_required),
            "unscored": list(self.unscored),
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
        }


class RubricGrader:
    """Grades an answer against an authorable rubric via an injected per-criterion scorer."""

    def __init__(self, scorer: JudgeFn) -> None:
        self.scorer = scorer

    def grade(self, task: str, answer: str, rubric: Rubric) -> GradedOutcome:
        """Score each criterion, compute the weighted outcome, and apply the required-criteria veto."""
        if not rubric.criteria:
            return GradedOutcome(weighted=0.0, passed=False, determinate=False)
        scores: dict[str, float] = {}
        failed_required: list[str] = []
        unscored: list[str] = []
        unreadable_required = False
        total_weight = 0.0
        weighted_sum = 0.0
        for criterion in rubric.criteria:
            raw = self.scorer(answer, task, criterion.text)
            if raw is None:
                # Out of both sums. Charging the answer for a criterion the grader could not read
                # would put the grader's flakiness into the answer's score, where an A/B would read
                # it as a difference between the arms.
                unscored.append(criterion.text)
                unreadable_required = unreadable_required or criterion.required
                continue
            score = _clamp(raw)
            scores[criterion.text] = score
            weight = max(0.0, criterion.weight)
            total_weight += weight
            weighted_sum += weight * score
            if criterion.required and score < rubric.required_gate:
                failed_required.append(criterion.text)
        weighted = weighted_sum / total_weight if total_weight else 0.0
        determinate = bool(scores) and not unreadable_required
        # An indeterminate outcome does not pass — but it does not count as a failure either, and
        # ``determinate`` is what tells the two apart.
        passed = determinate and weighted >= rubric.pass_threshold and not failed_required
        return GradedOutcome(
            scores=scores,
            weighted=weighted,
            passed=passed,
            failed_required=failed_required,
            unscored=unscored,
            determinate=determinate,
        )


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def model_grader(backend: object, model: str | None = None) -> RubricGrader:
    """Convenience: a RubricGrader whose per-criterion scores come from a model (reuses model_judge)."""
    return RubricGrader(model_judge(backend, model))


def grade_batch(
    grader: RubricGrader, rubric: Rubric, items: list[tuple[str, str]]
) -> list[bool | None]:
    """Grade many (task, answer) pairs into the pass/fail trials the A/B engine consumes.

    ``None`` marks an item the grader could not read — not a trial. Positions are preserved rather
    than compacted, because the honest A/B is **paired**: dropping an item from one arm and not the
    other misaligns every pair after it. A caller drops the pair, never one side of it.
    """
    trials: list[bool | None] = []
    for task, answer in items:
        outcome = grader.grade(task, answer, rubric)
        trials.append(outcome.passed if outcome.determinate else None)
    return trials
