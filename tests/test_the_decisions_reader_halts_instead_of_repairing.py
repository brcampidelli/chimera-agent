"""Study 24, item 3: the Decisions API reader halts on a malformed answer instead of repairing it.

The first reader turned a missing option into 0.0, clamped a Noul into [0, 1], and fell back to the
argmax when the written choice named no option. Keys spelled another way then read as ``p = 0`` —
"not dangerous" to the REVIEW band — a fail-OPEN. Each case below must now raise inside ``read``,
which the Decider records as a halt (``contract.py``: a backend that raises is a halt, never an answer).
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.decisions import Choice, Decider, Noul
from chimera.decisions.openrouter import OpenRouterDecisionsBackend

VERDICT = Choice("verdict", "Classify the action.", ("BLOCK", "REVIEW", "ALLOW"), event=("BLOCK", "REVIEW"))
DANGER = Noul("danger", "Would executing it be dangerous?")


def _read(answer: Any, question: Any = VERDICT) -> Any:
    backend = OpenRouterDecisionsBackend("k")
    return backend.read({"answers": {question.key: answer}}, question)


def test_a_well_formed_choice_still_reads() -> None:
    reading = _read({"choice": "REVIEW", "probabilities": {"BLOCK": 0.2, "REVIEW": 0.5, "ALLOW": 0.3}})
    assert reading.choice == "REVIEW" and reading.p == pytest.approx(0.7)


def test_rounding_inside_the_band_is_renormalized() -> None:
    reading = _read({"probabilities": {"BLOCK": 0.33, "REVIEW": 0.33, "ALLOW": 0.33}})
    assert sum(reading.shares.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "answer",
    [
        {"choice": "BLOCK", "probabilities": {"BLOCK": 0.9, "REVIEW": 0.1}},  # an option missing
        {"choice": "block", "probabilities": {"block": 0.9, "review": 0.05, "allow": 0.05}},  # keys spelled another way
        {"probabilities": {"BLOCK": 0.9, "REVIEW": 0.05, "ALLOW": 0.05, "MAYBE": 0.0}},  # an extra key
        {"probabilities": {"BLOCK": 1.4, "REVIEW": -0.2, "ALLOW": -0.2}},  # outside [0, 1]
        {"probabilities": {"BLOCK": 0.5, "REVIEW": 0.5, "ALLOW": 0.5}},  # sums to 1.5
        {"probabilities": {"BLOCK": "0.9", "REVIEW": 0.05, "ALLOW": 0.05}},  # not a number
        {"choice": "ESCALATE", "probabilities": {"BLOCK": 0.2, "REVIEW": 0.5, "ALLOW": 0.3}},  # choice names no option
        {"choice": "BLOCK"},  # no probabilities at all
    ],
)
def test_a_malformed_choice_raises(answer: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _read(answer)


@pytest.mark.parametrize("value", [1.3, -0.1, float("nan"), "0.7", True, None])
def test_a_malformed_noul_raises(value: Any) -> None:
    with pytest.raises(ValueError):
        _read({"noul": value}, DANGER)


def test_no_answer_for_the_question_raises() -> None:
    backend = OpenRouterDecisionsBackend("k")
    with pytest.raises(ValueError):
        backend.read({"answers": {"other": {"noul": 0.5}}}, DANGER)
    with pytest.raises(ValueError):
        backend.read({}, DANGER)


def test_the_decider_records_a_malformed_body_as_a_halt_not_a_verdict() -> None:
    class _Backend(OpenRouterDecisionsBackend):
        def ask(self, state: str, question: Any) -> Any:
            return self.read({"answers": {question.key: {"probabilities": {"block": 0.99, "review": 0.0, "allow": 0.01}}}}, question)

    answer = Decider(_Backend("k")).decide("adhoc", "rm -rf /", VERDICT)
    assert answer.halt and answer.p is None
