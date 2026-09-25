"""Tests for verifier-based sample selection (M14 B3 — Weaver-lite)."""

from __future__ import annotations

from chimera.fusion.consistency import SelfConsistency
from chimera.fusion.verifier_select import VerifierSelector, _parse_score, llm_scorer
from chimera.providers.gateway import CompletionResult, Message

# --- VerifierSelector -------------------------------------------------------------------


def test_selects_highest_scored() -> None:
    # A scorer that likes candidates containing "good".
    scorer = lambda task, ans: 1.0 if "good" in ans else 0.1  # noqa: E731
    sel = VerifierSelector([scorer]).select("t", ["bad one", "the good one", "meh"])
    assert sel.index == 1 and sel.answer == "the good one" and sel.score == 1.0


def test_ensemble_averages_scores() -> None:
    s1 = lambda t, a: 1.0 if a == "A" else 0.0  # noqa: E731
    s2 = lambda t, a: 1.0 if a == "B" else 0.0  # noqa: E731
    s3 = lambda t, a: 1.0 if a == "A" else 0.0  # noqa: E731
    # A scores mean 2/3, B scores 1/3 -> A wins.
    assert VerifierSelector([s1, s2, s3]).select("t", ["A", "B"]).answer == "A"


def test_broken_scorer_is_skipped() -> None:
    def boom(task: str, ans: str) -> float:
        raise RuntimeError("scorer down")

    good = lambda t, a: 0.9 if "x" in a else 0.2  # noqa: E731
    # The broken scorer is skipped; selection still works on the surviving one.
    assert VerifierSelector([boom, good]).select("t", ["y", "x"]).answer == "x"


def test_ties_break_by_order() -> None:
    flat = lambda t, a: 0.5  # noqa: E731 — everything scores equal
    assert VerifierSelector([flat]).select("t", ["first", "second"]).index == 0


def test_empty_scorers_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        VerifierSelector([])


def test_parse_score_normalizes() -> None:
    assert _parse_score("8") == 0.8
    assert _parse_score("The score is 10/10") == 1.0
    assert _parse_score("garbage") is None  # no number is an abstention, not a zero
    assert _parse_score("12") == 1.0  # clamped


# --- integration with SelfConsistency ---------------------------------------------------


class _Backend:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        content = self.answers.pop(0) if self.answers else "(none)"
        return CompletionResult(content=content, model="fake", prompt_tokens=1, completion_tokens=1)


def test_self_consistency_uses_selector_over_majority() -> None:
    # Majority would pick "dup" (2 votes); the verifier prefers "rare" — selector must win.
    backend = _Backend(["dup", "dup", "rare"])
    selector = VerifierSelector([lambda t, a: 1.0 if a == "rare" else 0.0])
    result = SelfConsistency(backend, n=3, selector=selector).complete(
        [Message(role="user", content="do it")]
    )
    assert result.content == "rare"


def test_llm_scorer_reads_a_grade() -> None:
    graded = _Backend(["7"])
    scorer = llm_scorer(graded)
    assert scorer("task", "an answer") == 0.7


# --- "could not tell" is not zero (study 25, defect 6) ------------------------------------------


def test_an_abstaining_scorer_does_not_drag_a_candidate_to_zero() -> None:
    """One grader abstains on the first candidate, the other grades both. Averaging the abstention
    in as 0.0 used to hand the pick to the second candidate on a grade nobody gave."""
    abstains_on_a = lambda t, a: None if a == "A" else 0.6  # noqa: E731
    grades = lambda t, a: 0.8 if a == "A" else 0.6  # noqa: E731
    sel = VerifierSelector([abstains_on_a, grades]).select("t", ["A", "B"])
    assert sel.answer == "A" and sel.score == 0.8


def test_a_graded_zero_still_beats_a_candidate_nobody_could_grade() -> None:
    graded_zero_or_abstain = lambda t, a: 0.0 if a == "graded" else None  # noqa: E731
    sel = VerifierSelector([graded_zero_or_abstain]).select("t", ["ungraded", "graded"])
    assert sel.answer == "graded" and sel.score == 0.0


def test_when_nobody_can_grade_the_first_candidate_is_kept_and_says_so() -> None:
    sel = VerifierSelector([lambda t, a: None]).select("t", ["first", "second"])
    assert sel.index == 0 and sel.score is None


def test_an_llm_grader_that_answers_without_a_number_abstains() -> None:
    assert llm_scorer(_Backend(["I cannot grade this."]))("task", "an answer") is None
