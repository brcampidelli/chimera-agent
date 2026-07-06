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
    assert _parse_score("garbage") == 0.0
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
