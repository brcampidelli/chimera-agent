"""The finder is asked for every defect with a confidence, and never told to keep to what matters.

Study 25 §2.9: a finder told to report "only important issues" filters in silence, and recall is
what it loses. The filtering belongs to the verifier, where each drop is recorded. So the finder's
text is checked for the narrowing words, and the verifier's for the two grounds that
`bench/review_judge` measured as too costly to ship (arm C, out of sample: +4.5 points of precision
for −35.9 of recall).
"""

from __future__ import annotations

import re

from chimera.review.finder import FINDER_SYSTEM, finder_request
from chimera.review.verifier import VERIFIER_SYSTEM

NARROWING = re.compile(
    r"\b(only|important|importance|significant|noteworthy|worth|most|relevant|major)\b", re.I
)


def test_the_finder_prompt_has_no_narrowing_word() -> None:
    found = NARROWING.findall(FINDER_SYSTEM) + NARROWING.findall(finder_request(""))

    assert found == []


def test_the_finder_asks_for_every_defect_and_a_confidence_for_each() -> None:
    assert "Report every defect you notice" in FINDER_SYSTEM
    assert "confidence" in FINDER_SYSTEM
    assert "unsure" in FINDER_SYSTEM
    assert "An empty findings list is a valid answer" in FINDER_SYSTEM


def test_the_verifier_keeps_under_doubt_on_the_two_cautious_grounds() -> None:
    assert "the code the finding describes is not in this diff" in VERIFIER_SYSTEM
    assert "a line of the diff contradicts the finding's central claim" in VERIFIER_SYSTEM
    assert "When your evidence falls short of either, keep it." in VERIFIER_SYSTEM


def test_the_verifier_does_not_carry_the_grounds_that_cost_a_third_of_the_recall() -> None:
    for ground in ("introduced", "pre-existing", "already there", "no defect", "praise",
                   "preference"):
        assert ground not in VERIFIER_SYSTEM.lower(), ground
