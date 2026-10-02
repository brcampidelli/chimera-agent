"""Two shapes a typed question can have that make its answer meaningless without any error. (1)
Two options with the same criterion: the rubric gives the model nothing to separate them, so which
one it picks is noise — an error, refused before any call. (2) A Noul whose "true" criterion is
itself a negation ("does not mention…"): yes then means "not X", the double negative that made
noul(X) + noul(not X) stop summing to 1 in the ecosystem's jev.nvim measurements — a warning,
because an error would refuse questions already in use.
"""

from __future__ import annotations

from chimera.decisions.contract import Choice, Noul
from chimera.decisions.lint import errors, lint


def test_two_options_with_one_criterion_is_an_error() -> None:
    q = Choice(key="k", instructions="Which one fits?", options=("alpha", "beta"),
               criteria={"alpha": "The file changed.", "beta": " the file changed "})
    assert any(f.code == "duplicate_criteria" for f in errors(q))


def test_distinct_criteria_are_clean() -> None:
    q = Choice(key="k", instructions="Which one fits?", options=("alpha", "beta"),
               criteria={"alpha": "The file changed.", "beta": "The file was created."})
    assert not any(f.code == "duplicate_criteria" for f in lint(q))


def test_a_negated_true_criterion_is_a_warning() -> None:
    q = Noul(key="k", instructions="Does the log report a failure?",
             criteria={"true": "does not report a failure", "false": "reports a failure"})
    found = lint(q)
    assert any(f.code == "negated_true" and f.severity == "warn" for f in found)
    assert not any(f.code == "negated_true" for f in errors(q))


def test_an_affirmative_true_criterion_is_clean() -> None:
    q = Noul(key="k", instructions="Does the log report a failure?",
             criteria={"true": "reports a failure", "false": "anything else"})
    assert not any(f.code == "negated_true" for f in lint(q))