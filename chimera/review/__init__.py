"""`chimera review`: a code review of a change, by a model from another family than its author.

Experimental. Study 25 (`bench/PLAN-study25-system-prompts.md` §2.9, §7 S15) set the shape, and
each piece of it answers a measured failure of a single judge:

- **Coverage and filtering are separate stages** (:mod:`.finder`, :mod:`.verifier`). A finder told
  to report only what matters filters silently; here every drop is recorded with its reason.
- **The filter is the cautious one.** `bench/review_judge` measured the stricter rubric out of
  sample at +4.5 points of precision for −35.9 points of recall on correct comments.
- **The reviewer is from another family** (:mod:`.family`), because self-preference replicates.
- **Findings first, P0–P3, each with ``file:line``, evidence and consequence**; "no findings" is
  said in words, with the residual risks and untested paths, and kept apart from a review that
  could not be completed (:mod:`.report`).

`bench/review_seeded` measures the finder's recall on defects seeded into real diffs from this
repository and what the verifier keeps.
"""

from __future__ import annotations

from chimera.review.diff import DiffError, ReviewDiff, collect, parse, untracked
from chimera.review.family import ReviewerChoice, choose_reviewer, model_family
from chimera.review.pipeline import review
from chimera.review.render import render_text
from chimera.review.report import SCHEMA, Finding, ReviewReport, Verdict
from chimera.review.verifier import CautiousVerifier, KeepAll, Verifier

__all__ = [
    "SCHEMA",
    "CautiousVerifier",
    "DiffError",
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
