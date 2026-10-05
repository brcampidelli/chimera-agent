"""`chimera review`: a code review of a change, by a model from another family than its author.

Experimental. Study 25 (`bench/PLAN-study25-system-prompts.md` §2.9, §7 S15) set the shape, and
each piece of it answers a measured failure of a single judge:

- **Coverage and filtering are separate stages** (:mod:`.finder`, :mod:`.verifier`). A finder told
  to report only what matters filters silently; here every drop is recorded with its reason.
- **The filter is the cautious one.** `bench/review_judge` measured the stricter rubric out of
  sample at +4.5 points of precision for −35.9 points of recall on correct comments.
- **The reviewer is from another family** (:mod:`.family`), for error coverage: different models
  miss different defects (arXiv 2610.01471). It once said "because self-preference replicates";
  that is contested (arXiv 2610.00369 finds no own-model premium), and the reviewer itself was
  chosen by `bench/review_reviewer`, not by that argument.
- **Findings first, P0–P3, each with ``file:line``, evidence and consequence**; "no findings" is
  said in words, with the residual risks and untested paths, and kept apart from a review that
  could not be completed (:mod:`.report`).
- **How much checking runs is a level** (:mod:`.effort`): ``low`` is the finder alone, ``high`` adds
  the verifier, and ``medium`` also cuts on the finder's confidence before verifying. The default
  is ``high``, because `bench/review_confidence_cut` selected a cut that a second set did not
  confirm.

`bench/review_seeded` measures the finder's recall on defects seeded into real diffs from this
repository and what the verifier keeps; `bench/review_reviewer` chose the default reviewer on the
same set (:data:`.family.MEASURED_REVIEWERS`).
"""

from __future__ import annotations

from chimera.review.diff import DiffError, ReviewDiff, collect, parse, untracked
from chimera.review.effort import DEFAULT_EFFORT, EFFORTS, Effort
from chimera.review.family import ReviewerChoice, choose_reviewer, model_family
from chimera.review.pipeline import review
from chimera.review.render import render_text
from chimera.review.report import SCHEMA, Finding, ReviewReport, Verdict
from chimera.review.verifier import CautiousVerifier, KeepAll, Verifier

__all__ = [
    "DEFAULT_EFFORT",
    "EFFORTS",
    "SCHEMA",
    "CautiousVerifier",
    "DiffError",
    "Effort",
    "Finding",
    "KeepAll",
    "ReviewDiff",
    "ReviewReport",
    "ReviewerChoice",
    "Verdict",
    "Verifier",
    "choose_reviewer",
    "collect",
    "model_family",
    "parse",
    "render_text",
    "review",
    "untracked",
]
