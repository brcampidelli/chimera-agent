"""The apparatus for the aggregation experiment, checked before it is allowed to cost anything.

The experiment is registered in `bench/fusion_aggregate/PREREGISTRATION.md`. What these tests hold is
the machinery — the ruler that reads a number out of a panel answer, the ceiling arm that decides
whether the paid stage may run at all, and the union that is the whole proposal.

Everything here is deterministic and free: no model call, no network, no dataset download.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
PREREG = REPO / "bench/fusion_aggregate/PREREGISTRATION.md"


def _runner() -> Any:
    """Load the runner without running the bench. It lives under `bench/`, not in the package."""
    sys.path.insert(0, str(REPO / "bench/llm_benchmarks"))
    spec = importlib.util.spec_from_file_location(
        "fusion_aggregate_runner", REPO / "bench/fusion_aggregate/run_aggregate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(gold: str, *answers: str) -> dict:
    return {
        "id": "x",
        "gold": gold,
        "question": "q",
        "answers": [{"model": f"m/{i}", "content": a} for i, a in enumerate(answers)],
    }


# --------------------------------------------------------------------------- the design


def test_the_paid_stage_is_gated_on_a_free_measurement() -> None:
    """The rule this project wrote after paying for a retry loop around a verifier that accepted
    95% of what it saw: measure how much there is to reject before building the rejecter.

    Here that is free — the ceiling and the vote both come out of the cached panel — so a headroom
    below the registered floor closes the item at the price of the panel alone.
    """
    text = PREREG.read_text(encoding="utf-8")
    source = (REPO / "bench/fusion_aggregate/run_aggregate.py").read_text(encoding="utf-8")

    assert "headroom < 3.0 pp" in text and "stage 3 is not run" in text
    assert "if headroom < 3.0:" in source, "the gate is written down but not enforced"
    assert "--stage3" in source, "the paid stage must be opt-in"


def test_the_criterion_is_absolute_and_counts_the_damage() -> None:
    """A net win that destroys more than it saves is a different thing from one that only adds, and
    the net alone cannot tell them apart — the failure that shipped a +12.9 pp headline while
    silently breaking 35 cases."""
    text = PREREG.read_text(encoding="utf-8")

    assert "≥ +3.0 pp" in text and "absolute floor, never a multiplicative one" in text
    assert "damage ≤ gain" in text
    assert "CANNOT show" in text


def test_the_corpus_is_the_branch_the_experiment_replaces() -> None:
    """The vote only ever runs on a logic-typed task. Measuring it on anything else would compare
    an aggregator against a branch that never fires."""
    source = (REPO / "bench/fusion_aggregate/run_aggregate.py").read_text(encoding="utf-8")

    assert 'classify_task_type([{"role": "user", "content": row["question"]}]) == "logic"' in source
    assert "random.Random(seed).sample" in source, "the draw must be seeded, not incidental"


# --------------------------------------------------------------------------- the ruler


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("blah blah\nANSWER: 42", "42"),
        ("ANSWER: 1,000", "1000"),
        ("ANSWER: 18.0", "18"),
        ("ANSWER: -7", "-7"),
        ("no marker here, so the last number wins: 13", "13"),
        ("nothing numeric at all", None),
    ],
)
def test_the_number_is_read_the_same_way_for_every_arm(text: str, expected: str | None) -> None:
    """Every arm is scored by this one function. A ruler that reads one arm's phrasing better than
    another's decides the experiment by formatting."""
    assert _runner().extract(text) == expected


def test_a_panel_that_ignored_the_format_stops_the_run(tmp_path: Path) -> None:
    """`§2e`: a harness written against one model stops reporting the moment the model changes, and
    it stops silently. Here the symptom would be every arm looking wrong at once."""
    run = _runner()
    rows = [_row("42", "the answer is 42", "42 obviously", "I think 42")]

    assert run.extraction_rate(rows) == 0.0
    assert run._MIN_EXTRACTION == 0.90
    source = (REPO / "bench/fusion_aggregate/run_aggregate.py").read_text(encoding="utf-8")
    assert "raise SystemExit(" in source and "_MIN_EXTRACTION" in source


def test_a_dead_panelist_is_data_and_not_a_crash() -> None:
    run = _runner()
    rows = [_row("42", "ANSWER: 42", "ANSWER: 42")]
    rows[0]["answers"].append({"model": "m/2", "content": "", "error": "429"})

    free = run.arms_free(rows)

    assert free["oracle"] == [True]
    assert free["member:2"] == [False], "a panelist that never answered did not get the answer right"


# --------------------------------------------------------------------------- the arms


def test_the_ceiling_finds_a_correct_answer_the_vote_throws_away() -> None:
    """The whole hypothesis in one row: two panelists agree on the wrong number, one holds the right
    one. Both votes return the majority — wrong — and the pool held the answer the entire time.

    This is the Barrel-of-Monkeys gap made local: pool coverage 80.8% against 66.2% selected.
    """
    run = _runner()
    rows = [_row("42", "ANSWER: 41", "ANSWER: 41", "ANSWER: 42")]

    free = run.arms_free(rows)

    assert free["oracle"] == [True], "the correct answer was in the pool"
    assert free["vote_answer"] == [False], "and the majority discarded it"


def test_the_shipped_vote_can_elect_the_minority_answer() -> None:
    """Measured in the shipped code before this bench existed, and kept here as the size of the bug.

    `majority` clusters by difflib over the WHOLE answer, so three answers that differ in one digit
    are one cluster, and its representative is the LONGEST member rather than the most common one.
    Two panelists said 42; the vote returns 41. `chimera/fusion/task_type.py` opens by saying the
    branch exists so "a correct minority answer must not be averaged away" — and it can elect one.
    """
    run = _runner()
    rows = [_row(
        "42",
        "Work: 48 + 24 = 72 clips altogether in the two months here.\nANSWER: 41",
        "Work: 48 + 24 = 72 clips altogether in the two months.\nANSWER: 42",
        "Work: 48 + 24 = 72 clips altogether in the two months.\nANSWER: 42",
    )]

    free = run.arms_free(rows)

    assert free["vote_text"] == [False], "the shipped vote stopped electing the minority"
    assert free["vote_answer"] == [True], "counting the number does not recover the majority"


def test_agreement_reached_by_different_reasoning_is_a_majority() -> None:
    """The other direction of the same bug: three panelists all reach 72 and the shipped vote sees
    no majority, so a logic task with unanimous agreement pays for a judge and a synthesiser."""
    run = _runner()
    rows = [_row(
        "72",
        "April: 48. May: half of that, 24. Total 72.\nANSWER: 72",
        "She sold forty-eight the first month and half as many the second, so seventy-two.\nANSWER: 72",
        "Let x = 48. May = x/2 = 24. x + x/2 = 72.\nANSWER: 72",
    )]

    free = run.arms_free(rows)

    assert free["vote_text"] == [False], "the shipped vote started seeing this majority"
    assert free["vote_answer"] == [True]


def test_the_vote_and_the_ceiling_agree_when_there_is_nothing_to_win() -> None:
    """The other side of the same guard: if the ceiling never rises above the vote, the headroom is
    zero and the pre-registration closes the item without paying for stage 3."""
    run = _runner()
    rows = [_row("42", "ANSWER: 42", "ANSWER: 42", "ANSWER: 42")]

    free = run.arms_free(rows)

    assert free["oracle"] == [True] and free["vote_answer"] == [True]


def test_the_union_keeps_the_cluster_of_one() -> None:
    """`majority` returns one cluster and only when it holds more than half. The union keeps every
    cluster — including the single-member one, which is exactly where a correct minority answer
    lives and the only reason this experiment exists."""
    run = _runner()

    kept = run.union(["ANSWER: 41", "ANSWER: 41", "ANSWER: 42"])

    assert len(kept) == 2, f"the minority answer was merged away: {kept}"
    assert any("42" in candidate for candidate in kept)


def test_the_paid_arm_is_measured_against_the_FIXED_vote() -> None:
    """Fixing the clustering and adding a validator are two interventions. Measuring them together
    would credit the validator with the fix — `§2g` in the form that flatters the proposal."""
    text = PREREG.read_text(encoding="utf-8")

    assert "union_validator − vote_answer ≥ +3.0 pp" in text
    assert "oracle − vote_answer" in text, "the headroom gate must use the fixed vote too"


def test_the_union_of_one_agreeing_panel_is_one_candidate() -> None:
    """A validator handed three copies of the same answer has nothing to select, and the arm must
    reduce to the vote rather than pretend to have chosen."""
    assert len(_runner().union(["ANSWER: 42", "ANSWER: 42", "ANSWER: 42"])) == 1
