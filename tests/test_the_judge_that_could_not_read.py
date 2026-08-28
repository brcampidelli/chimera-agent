"""A judge that could not read must not be recorded as a judge who read and refused.

``model_judge`` asks a model for a number in [0, 1] and used to return ``0.0`` whenever the reply
held no number. A refusal, an empty completion, a provider error string, an answer in the wrong
language — all of them came out as the lowest possible score, which is indistinguishable from a
judge that read the work and failed it. Downstream, ``Manager(use_rubric=True)`` compares that
score against a threshold, ``0.0`` is below every threshold, and under verify-or-revert the
workspace is restored: correct work deleted because a model replied in prose.

The same shape sat one layer up, on the path that is actually on by default. ``_parse_verdict``
ended in ``Review(approved=False, feedback=verdict)``, so a **blank** reply was a rejection whose
stated reason was the empty string — while the docstring directly above it already named this exact
harm ("would revert correct work") as the thing it existed to prevent.

The fix is one distinction, made in three places: ``None`` is not zero, an abstention is not a
verdict, and a reviewer who abstained is not an authority the receipt may name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core import AutonomousAgent, AutonomousConfig
from chimera.core.agent import AgentResult
from chimera.core.supervisor import Manager, Review
from chimera.eval.rubric import Dimension, evaluate_cascade, model_judge
from chimera.eval.rubric_grade import Criterion, Rubric, RubricGrader, grade_batch


class _Backend:
    """A model that replies with whatever it was handed."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, messages: Any, model: Any = None, **kwargs: Any) -> Any:
        class _R:
            content = self.reply

        return _R()


# --------------------------------------------------------------------------- model_judge


def test_a_judge_that_answered_nothing_scores_nothing() -> None:
    """The defect itself. Each of these replies used to come back as 0.0."""
    for reply in ("", "I cannot evaluate this.", "Erro: provedor indisponivel", "   \n  "):
        assert model_judge(_Backend(reply))("answer", "task", "criterion") is None, reply


def test_a_judge_that_did_answer_is_still_read() -> None:
    """The control. Narrowing what counts as a score must not stop scores being read — without
    this, a judge returning ``None`` for everything would pass the test above and be useless."""
    assert model_judge(_Backend("0.8"))("a", "t", "c") == 0.8
    assert model_judge(_Backend("Score: 1.0 — meets it"))("a", "t", "c") == 1.0
    assert model_judge(_Backend("0.0"))("a", "t", "c") == 0.0  # a real zero is still a real zero


# --------------------------------------------------------------------------- the cascade


def _dim(name: str, weight: float, value: float | None) -> Dimension:
    return Dimension(name, weight, lambda a, t: value)


def test_an_unreadable_dimension_leaves_both_sums() -> None:
    """Out of the numerator AND the denominator. In the denominator it would still drag the
    overall down, which is the same punishment applied more quietly."""
    result = evaluate_cascade(
        "a", "t", [_dim("first", 0.5, 1.0), _dim("second", 0.5, None)]
    )
    assert result.unscored == ["second"]
    assert result.scores == {"first": 1.0}
    assert result.overall == 1.0  # 0.5*1.0 / 0.5 — not 0.5, which is what halving would give


def test_a_gated_dimension_stays_in_the_denominator() -> None:
    """The control that protects the cascade's whole point, and the mistake I made first: a
    dimension the cascade gated away contributes zero out of its **full** weight, because that
    penalty is what a gate is for. Only unreadable ones may leave the denominator."""
    result = evaluate_cascade(
        "a", "t", [_dim("first", 0.5, 0.4), _dim("second", 0.5, 1.0)]
    )
    assert result.stopped_at == "first"
    assert result.overall == 0.2  # 0.5*0.4 / 1.0 — the gated half is still charged for


def test_an_unreadable_dimension_does_not_gate_the_cascade() -> None:
    """Stopping on a non-answer would deny every later dimension its say for a reason that says
    nothing about the answer."""
    result = evaluate_cascade(
        "a", "t", [_dim("first", 0.5, None), _dim("second", 0.5, 1.0)]
    )
    assert result.stopped_at is None
    assert result.scores == {"second": 1.0}


def test_nothing_readable_is_not_a_score_of_zero() -> None:
    """``None``, deliberately, and not 0.0: the production caller compares this against a
    threshold, and ``0.0`` silently loses that comparison while ``None`` cannot be compared at
    all. mypy then forces every caller to say what it does about it."""
    result = evaluate_cascade("a", "t", [_dim("only", 1.0, None)])
    assert result.overall is None
    assert result.unscored == ["only"]


# --------------------------------------------------------------------------- the Manager


def test_the_rubric_manager_abstains_instead_of_rejecting() -> None:
    """The end-to-end of the reported defect: this same call used to return ``approved=False``."""
    review = Manager(_Backend("I cannot evaluate this."), use_rubric=True).review("t", "result")
    assert review.abstained is True
    assert review.approved is True  # a reviewer that did not review must not veto


def test_the_rubric_manager_still_rejects_work_it_did_read() -> None:
    """The control. Abstention must not become a way of never failing anything."""
    review = Manager(_Backend("0.1"), use_rubric=True).review("t", "result")
    assert review.approved is False
    assert review.abstained is False


def test_a_blank_verdict_abstains_on_the_default_path() -> None:
    """``use_rubric`` is off unless somebody passes ``--rubric``, so this is the path that runs.
    An empty reply used to be a rejection whose stated reason was the empty string."""
    review = Manager(_Backend("   \n  ")).review("t", "result")
    assert review.abstained is True
    assert review.approved is True


def test_prose_without_the_keyword_is_still_a_revision() -> None:
    """Pinned as a control, because it is a deliberate decision this repository already made:
    "This is wrong because X" is a rejection that merely missed the format. Widening the
    abstention to cover it would throw away a real signal to fix a different bug."""
    review = Manager(_Backend("This is wrong because X")).review("t", "result")
    assert review.approved is False
    assert review.abstained is False


# --------------------------------------------------------------------------- the grader


def test_an_unreadable_criterion_leaves_the_weighted_mean() -> None:
    scores: dict[str, float | None] = {"read": 1.0, "unread": None}
    grader = RubricGrader(lambda answer, task, criterion: scores[criterion])
    outcome = grader.grade("t", "a", Rubric([Criterion("read"), Criterion("unread")]))
    assert outcome.unscored == ["unread"]
    assert outcome.weighted == 1.0
    assert outcome.determinate is True
    assert outcome.passed is True


def test_an_unreadable_required_criterion_is_an_absence_not_a_failure() -> None:
    """A required criterion is the one the outcome is void without. Unread, it leaves no verdict —
    and ``determinate`` is what stops that being filed as the answer having missed it.

    Scored alongside a readable criterion on purpose. With *everything* unreadable the outcome is
    indeterminate for the duller reason that nothing scored at all, and a sabotage run proved that
    version of the test passes with the required-criterion rule deleted — it was pinning the wrong
    clause. Here the readable criterion would otherwise carry the whole grade to a pass.
    """
    scores: dict[str, float | None] = {"nice": 1.0, "must": None}
    grader = RubricGrader(lambda answer, task, criterion: scores[criterion])
    outcome = grader.grade(
        "t", "a", Rubric([Criterion("nice"), Criterion("must", required=True)])
    )
    assert outcome.weighted == 1.0  # the readable half is perfect...
    assert outcome.determinate is False  # ...and the outcome is still not a verdict
    assert outcome.passed is False
    assert outcome.failed_required == []  # it did not fail; it was never checked
    assert outcome.unscored == ["must"]


def test_nothing_readable_at_all_is_also_indeterminate() -> None:
    """The duller neighbour of the case above, kept separate so neither hides the other."""
    grader = RubricGrader(lambda answer, task, criterion: None)
    outcome = grader.grade("t", "a", Rubric([Criterion("only")]))
    assert outcome.determinate is False
    assert outcome.passed is False
    assert outcome.scores == {}


def test_grade_batch_keeps_the_position_of_an_ungradeable_item() -> None:
    """``None`` in place, not a compacted list. The honest A/B is paired, so dropping an item from
    one arm and not the other misaligns every pair after it."""
    scores = {"good": 1.0, "bad": 0.0, "silent": None}
    grader = RubricGrader(lambda answer, task, criterion: scores[answer])
    trials = grade_batch(
        grader, Rubric([Criterion("c")]), [("t", "good"), ("t", "silent"), ("t", "bad")]
    )
    assert trials == [True, None, False]


# --------------------------------------------------------------------------- the wiring


class _Worker:
    def __init__(self, workspace: Path | None = None) -> None:
        self.runs = 0
        self.workspace = workspace

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        return AgentResult(answer="summarised the architecture", steps=1, stopped_reason="final")


class _AbstainingManager:
    """A manager configured, asked, and unable to answer — the state the whole file is about."""

    def review(self, task: str, answer: str, context: str) -> Review:
        return Review(approved=True, abstained=True)


def _agent(manager: Any) -> AutonomousAgent:
    return AutonomousAgent(
        worker=_Worker(),
        manager=manager,
        planner=None,
        verifier=None,
        guard=None,
        config=AutonomousConfig(max_attempts=1, use_planner=False),
    )


def test_an_abstaining_reviewer_does_not_fail_the_work() -> None:
    """Mechanism to wiring. Everything above is unreachable if the agent still reads the
    abstention as a verdict."""
    result = _agent(_AbstainingManager()).run("summarise the architecture")
    assert result.success is True


def test_an_abstaining_reviewer_is_not_named_as_the_authority() -> None:
    """The other half, and the one that would rot silently: approving is only correct if the
    receipt stops claiming a review happened. ``evidence="manager"`` here would be the same
    fabrication-by-omission ``_manager_ran`` was written to stop, one step further along."""
    result = _agent(_AbstainingManager()).run("summarise the architecture")
    assert result.attempts[-1].evidence == "none"


def test_abstention_beats_disapproval_whoever_wrote_the_review() -> None:
    """``Manager`` always pairs ``abstained`` with ``approved=True``, so nothing in this repository
    reaches the branch that enforces it — and an uncovered branch is a branch that can rot. A
    Manager is an injected collaborator, and any reviewer saying "I did not judge" must not veto,
    including one that filled in ``approved`` carelessly."""

    class _ContradictoryManager:
        def review(self, task: str, answer: str, context: str) -> Review:
            return Review(approved=False, feedback="whatever", abstained=True)

    result = _agent(_ContradictoryManager()).run("summarise the architecture")
    assert result.success is True
    assert result.attempts[-1].evidence == "none"


def test_a_reviewer_that_did_judge_is_still_named() -> None:
    """The control for the two above."""

    class _Approving:
        def review(self, task: str, answer: str, context: str) -> Review:
            return Review(approved=True)

    result = _agent(_Approving()).run("summarise the architecture")
    assert result.attempts[-1].evidence == "manager"
