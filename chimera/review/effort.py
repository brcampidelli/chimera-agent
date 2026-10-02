"""How much checking a review does: ``low``, ``medium`` or ``high``.

- **low:** the finder alone. Every located finding is shown; no verifier call is made.
- **medium:** the finder, then a cut on the finder's own confidence, then the verifier on what the
  cut keeps. Cutting first spares the verifier's calls on findings it would hide anyway.
- **high:** the finder and the verifier, with no cut. This is what ``chimera review`` did before
  effort levels existed.

**Why the default is high, and the cut is 0.8.** `bench/review_confidence_cut` re-read the stored
reviews of `bench/review_reviewer` and `bench/review_seeded` under a rule frozen before the
analysis. On the selection set 0.8 kept 97/100 seeded defects and removed 64% of the findings on
clean diffs, so the rule chose it. On the confirmation set it kept 39/45 = 86.7% of the seeds,
under the 90% bar: glm-5.3 writes lower confidences on true findings than the models it was chosen
on. The rule's outcome for "selected, not confirmed" is the one shipped here: medium exists with
the selected cut, opt-in, and the default stays today's behaviour.

The cut lives here, in code, and never in the finder's prompt: a finder told to keep to what it is
sure of filters in silence (`tests/test_the_review_finder_is_never_told_to_narrow.py`), while a cut
in the pipeline records every finding it hides, with its stage and reason.
"""

from __future__ import annotations

from typing import Literal

Effort = Literal["low", "medium", "high"]
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high")

#: `bench/review_confidence_cut`: a cut was selected and not confirmed, so the default is unchanged.
DEFAULT_EFFORT: Effort = "high"
#: The cut that bench selected. Applies only at ``medium``.
MEDIUM_CONFIDENCE_CUT = 0.8


def confidence_cut(effort: Effort) -> float | None:
    """The cut a level applies, or ``None`` for no cut."""
    return MEDIUM_CONFIDENCE_CUT if effort == "medium" else None


def verifies(effort: Effort) -> bool:
    """Whether a level runs the second-stage verifier."""
    return effort != "low"
