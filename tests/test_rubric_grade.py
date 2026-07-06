"""Tests for authorable rubric grading (M14 D1)."""

from __future__ import annotations

from chimera.eval.rubric_grade import (
    Criterion,
    GradedOutcome,
    Rubric,
    RubricGrader,
    grade_batch,
    model_grader,
)
from chimera.providers.gateway import CompletionResult


def _fixed_scorer(mapping: dict[str, float]):
    """A scorer that returns a preset score per criterion text (default 1.0)."""
    return lambda answer, task, criterion: mapping.get(criterion, 1.0)


# --- weighting ---------------------------------------------------------------------------


def test_weighted_score_respects_weights() -> None:
    rubric = Rubric([Criterion("a", weight=3.0), Criterion("b", weight=1.0)])
    grader = RubricGrader(_fixed_scorer({"a": 1.0, "b": 0.0}))
    outcome = grader.grade("t", "ans", rubric)
    assert outcome.weighted == 0.75  # (3*1 + 1*0) / 4
    assert outcome.passed is True  # 0.75 >= 0.6, no required veto


def test_pass_threshold_boundary() -> None:
    rubric = Rubric([Criterion("only")], pass_threshold=0.6)
    assert RubricGrader(_fixed_scorer({"only": 0.6})).grade("t", "a", rubric).passed is True
    assert RubricGrader(_fixed_scorer({"only": 0.59})).grade("t", "a", rubric).passed is False


# --- required veto -----------------------------------------------------------------------


def test_required_criterion_vetoes_high_score() -> None:
    rubric = Rubric(
        [Criterion("nice", weight=9.0), Criterion("must", weight=1.0, required=True)],
        required_gate=0.5,
    )
    grader = RubricGrader(_fixed_scorer({"nice": 1.0, "must": 0.2}))
    outcome = grader.grade("t", "a", rubric)
    assert outcome.weighted >= 0.9  # weighted score is high...
    assert outcome.passed is False  # ...but the required criterion failed -> veto
    assert outcome.failed_required == ["must"]


def test_required_criterion_met_passes() -> None:
    rubric = Rubric([Criterion("must", required=True)], required_gate=0.5)
    outcome = RubricGrader(_fixed_scorer({"must": 0.8})).grade("t", "a", rubric)
    assert outcome.passed is True and outcome.failed_required == []


# --- edges -------------------------------------------------------------------------------


def test_empty_rubric_fails_safely() -> None:
    outcome = RubricGrader(_fixed_scorer({})).grade("t", "a", Rubric([]))
    assert outcome.passed is False and outcome.weighted == 0.0


def test_scores_are_clamped() -> None:
    rubric = Rubric([Criterion("hi"), Criterion("lo")])
    grader = RubricGrader(_fixed_scorer({"hi": 1.5, "lo": -0.4}))
    outcome = grader.grade("t", "a", rubric)
    assert outcome.scores == {"hi": 1.0, "lo": 0.0}  # clamped into [0, 1]


def test_zero_weight_criterion_ignored_in_mean() -> None:
    rubric = Rubric([Criterion("real", weight=1.0), Criterion("noise", weight=0.0)])
    grader = RubricGrader(_fixed_scorer({"real": 1.0, "noise": 0.0}))
    assert grader.grade("t", "a", rubric).weighted == 1.0  # zero-weight doesn't drag the score


# --- serialization + batch ---------------------------------------------------------------


def test_rubric_roundtrips_through_dict() -> None:
    rubric = Rubric(
        [Criterion("x", weight=2.0, required=True)], pass_threshold=0.7, required_gate=0.4
    )
    restored = Rubric.from_dict(rubric.to_dict())
    assert restored.pass_threshold == 0.7 and restored.required_gate == 0.4
    assert restored.criteria[0].required is True and restored.criteria[0].weight == 2.0


def test_from_list_builds_criteria() -> None:
    rubric = Rubric.from_list(
        [{"text": "a"}, {"text": "b", "weight": 2, "required": True}], pass_threshold=0.5
    )
    assert len(rubric.criteria) == 2
    assert rubric.criteria[1].required is True
    assert rubric.pass_threshold == 0.5


def test_grade_batch_produces_bench_trials() -> None:
    rubric = Rubric([Criterion("ok")], pass_threshold=0.6)
    # scorer keys off the ANSWER text via the task closure is awkward; use answer-sensitive scorer.
    grader = RubricGrader(lambda answer, task, criterion: 1.0 if answer == "good" else 0.0)
    trials = grade_batch(grader, rubric, [("t1", "good"), ("t2", "bad"), ("t3", "good")])
    assert trials == [True, False, True]  # exactly the pass/fail list the A/B consumes


def test_outcome_summary_shape() -> None:
    outcome = GradedOutcome(scores={"a": 0.5}, weighted=0.5, passed=False, failed_required=["a"])
    summary = outcome.summary()
    assert summary["passed"] is False and summary["failed_required"] == ["a"]
    assert summary["scores"] == {"a": 0.5}


# --- model-backed grader -----------------------------------------------------------------


def test_model_grader_scores_from_backend() -> None:
    class _Backend:
        def complete(self, messages: object, **kwargs: object) -> CompletionResult:
            return CompletionResult(content="0.8", model="fake")

    grader = model_grader(_Backend())
    outcome = grader.grade("task", "answer", Rubric([Criterion("c")]))
    assert outcome.scores["c"] == 0.8 and outcome.passed is True
