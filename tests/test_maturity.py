"""Tests for the maturity scorecard (M15-B5)."""

from __future__ import annotations

from pathlib import Path

from chimera.eval.maturity import (
    CHIMERA_TAXONOMY,
    Coverage,
    Surface,
    evidence_from_tests,
    format_scorecard,
    score,
    score_repo,
)

_TOY = [
    Surface("alpha", [
        Coverage("a.one", "one", "test_one"),
        Coverage("a.two", "two", "test_two"),
    ]),
    Surface("beta", [
        Coverage("b.one", "one", "test_three"),
    ]),
]


def test_scores_proven_and_missing() -> None:
    card = score(_TOY, present={"test_one", "test_three"})  # test_two absent
    alpha = next(s for s in card.surfaces if s.name == "alpha")
    beta = next(s for s in card.surfaces if s.name == "beta")
    assert alpha.proven == 1 and alpha.total == 2 and alpha.missing == ["a.two"]
    assert beta.proven == 1 and beta.total == 1 and beta.missing == []


def test_levels_by_threshold() -> None:
    full = score([Surface("s", [Coverage("x", "", "t1")])], present={"t1"})
    assert full.surfaces[0].level == "present"
    empty = score([Surface("s", [Coverage("x", "", "t1"), Coverage("y", "", "t2")])], present=set())
    assert empty.surfaces[0].level == "sparse"
    half = score(
        [Surface("s", [Coverage("x", "", "t1"), Coverage("y", "", "t2")])], present={"t1"}
    )
    assert half.surfaces[0].level == "partial"  # exactly 50%


def test_a_full_score_is_not_called_a_release_grade() -> None:
    """Every input here is a file NAME. A band called "GA" read as "generally available", a claim
    about the product that a glob of test-file names cannot carry: a test that exists can fail, be
    skipped, or test something else. The band now names what was counted, and the rendering says so.
    """
    card = score(_TOY, present={"test_one", "test_two", "test_three"})
    out = format_scorecard(card)

    assert {card.level, *(s.level for s in card.surfaces)} == {"present"}
    for release_word in ("GA", "Beta", "Alpha"):
        assert release_word not in out
    assert "test file" in out


def test_weakest_surface_is_the_objective() -> None:
    card = score(_TOY, present={"test_three"})  # alpha 0/2, beta 1/1
    weak = card.weakest()
    assert weak is not None and weak.name == "alpha"


def test_weakest_is_none_when_complete() -> None:
    card = score(_TOY, present={"test_one", "test_two", "test_three"})
    assert card.weakest() is None
    assert card.ratio == 1.0 and card.level == "present"


def test_evidence_from_tests_globs_stems(tmp_path: Path) -> None:
    (tmp_path / "test_foo.py").write_text("", encoding="utf-8")
    (tmp_path / "test_bar.py").write_text("", encoding="utf-8")
    (tmp_path / "helper.py").write_text("", encoding="utf-8")  # not a test_ file
    assert evidence_from_tests(tmp_path) == {"test_foo", "test_bar"}


def test_format_scorecard_renders() -> None:
    out = format_scorecard(score(_TOY, present={"test_one"}))
    assert "maturity" in out and "alpha" in out and "weakest surface" in out


# --- the real taxonomy against the real tests dir ----------------------------------------


def test_default_taxonomy_scores_against_the_real_repo() -> None:
    """The default taxonomy must map to REAL tests — a healthy repo scores high, no phantom gaps."""
    tests_dir = Path(__file__).resolve().parent
    card = score_repo(tests_dir)
    # Every coverage-ID in the shipped taxonomy points at a test that actually exists.
    all_missing = [m for s in card.surfaces for m in s.missing]
    assert all_missing == [], f"taxonomy references non-existent tests: {all_missing}"
    # Every coverage-ID has its test file. That is all this proves: presence, not passing.
    assert card.level == "present"


def test_taxonomy_ids_are_unique() -> None:
    ids = [c.id for surface in CHIMERA_TAXONOMY for c in surface.coverage]
    assert len(ids) == len(set(ids))
