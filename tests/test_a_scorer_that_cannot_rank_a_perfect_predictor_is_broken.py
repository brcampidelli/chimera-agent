"""The false-success bench's scorer, pinned — the part of `bench/false_success` that outlives it.

The measurement said DO NOT BUILD (`bench/false_success/RESULTS.md`), so no detector shipped and
there is no production behaviour to guard. What does need guarding is the ruler: every number in
that file rests on `within_task_auroc` separating three things that all look like "about 0.5" from
the outside — a perfect predictor, a coin, and an arm with nothing to read. A silent edit there
would not fail anything, it would just quietly change what the published null means.

So the two controls of PREREGISTRATION §8 run here, on synthetic rows, with no corpus required:

* a perfect predictor must score exactly 1.000 — a scorer that cannot do that is broken, and then
  no other number means anything;
* a constant predictor must score 0.500 **and be reported as degenerate**, because "it had nothing
  to read" and "it read and did no better than chance" are different claims (Bee §2f).

Plus the trap that nearly put a wrong finding in RESULTS.md: `delivered_matches_verified` is
tri-state, and coercing it to bool encodes "we could not look" as "we looked and it did not match".
"""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parent.parent / "bench" / "false_success"


def _load(name: str):
    """Load a bench module by path.

    The name goes into `sys.modules` because `detect.py` does `from corpus import Solve` and the
    bench runs with its own directory as cwd. Registering the object is enough for that to resolve —
    `sys.path` is deliberately left alone, so a module called `run` or `corpus` here cannot shadow
    anything for the rest of the suite.
    """
    spec = importlib.util.spec_from_file_location(name, _BENCH / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


corpus = _load("corpus")
detect = _load("detect")
run = _load("run")


def _solve(task: str, oracle: float, *, claim: str = "done", self_report: bool = True):
    return corpus.Solve(
        task=task,
        hid="arm-000-r0",
        claim=claim,
        self_report=self_report,
        oracle=oracle,
        rounds=1,
        attempts=1,
        reverted=not self_report,
        stagnant=False,
        delivered_matches_verified=True if self_report else None,
    )


def _rows(task: str, scores_and_labels: list[tuple[float, bool]]) -> list[tuple[str, float, bool]]:
    return [(task, score, positive) for score, positive in scores_and_labels]


def test_a_perfect_predictor_scores_exactly_one() -> None:
    """The positive control. If this slips, every figure in RESULTS.md is unanchored."""
    scored = _rows("t", [(9.0, True), (8.0, True), (1.0, False), (0.0, False)])
    auroc, per_task = run.within_task_auroc(scored)
    assert auroc == 1.0
    assert per_task["t"] == (4.0, 4)


def test_a_perfectly_wrong_predictor_scores_exactly_zero() -> None:
    """The same ruler read from the other end — an arm below 0.5 is informative with a flipped sign,
    which is the whole reason `length` (0.398) and `length_ceiling` (0.602) are reported as a pair."""
    scored = _rows("t", [(0.0, True), (1.0, False)])
    auroc, _ = run.within_task_auroc(scored)
    assert auroc == 0.0


def test_ties_count_one_half_so_a_constant_arm_lands_on_the_coin() -> None:
    scored = _rows("t", [(5.0, True), (5.0, False), (5.0, True), (5.0, False)])
    auroc, _ = run.within_task_auroc(scored)
    assert auroc == 0.5


def test_a_task_whose_label_never_varies_contributes_no_pairs() -> None:
    """The design claim of PREREGISTRATION §3: a constant-label task is not averaged in as a zero,
    it is absent. Eleven of the corpus's 23 tasks are in this state."""
    scored = _rows("constant", [(1.0, True), (2.0, True)]) + _rows("varies", [(2.0, True), (1.0, False)])
    auroc, per_task = run.within_task_auroc(scored)
    assert "constant" not in per_task
    assert per_task["varies"] == (1.0, 1)
    assert auroc == 1.0


def test_an_arm_with_nothing_to_read_is_reported_as_degenerate_not_as_a_coin() -> None:
    """`harness` scored 0.5000 on the primary because four of its five features were constant among
    claimed successes — not because it read them and did no better than chance."""
    population = [_solve("t", 1.0), _solve("t", 0.1), _solve("u", 1.0), _solve("u", 0.1)]
    flat = run.evaluate(detect.SelfReport, population, random.Random(0))
    assert flat["degenerate"] is True
    assert flat["auroc"] == 0.5

    telling = [
        _solve("t", 1.0, claim="short"),
        _solve("t", 0.1, claim="a much longer claim than the other one"),
        _solve("u", 1.0, claim="short"),
        _solve("u", 0.1, claim="a much longer claim than the other one"),
    ]
    assert run.evaluate(detect.LengthCeiling, telling, random.Random(0))["degenerate"] is False


def test_shuffling_labels_within_a_task_leaves_that_task_pass_rate_alone() -> None:
    """The negative control has to permute *inside* the task. Shuffling across tasks would change
    each task's pass rate and so change the thing the shuffle is supposed to hold fixed."""
    population = [_solve("t", 1.0), _solve("t", 0.1), _solve("u", 1.0), _solve("u", 1.0)]
    shuffled = run.shuffled_within_task(population, random.Random(7))
    for task in ("t", "u"):
        before = sorted(s.oracle for s in population if s.task == task)
        after = sorted(s.oracle for s in shuffled if s.task == task)
        assert before == after


def test_delivered_matches_verified_keeps_three_levels_because_none_is_not_false() -> None:
    """The coercion that manufactured a 547/547 agreement and a retracted finding (RESULTS.md §5).

    `None` means there was no winning attempt to compare a digest against. Folding it into `False`
    claims the delivered tree diverged from the verified one, which is a different — and much
    louder — statement than "not checkable".
    """
    levels = {
        state: detect.HarnessSignals.features(
            corpus.Solve(
                task="t",
                hid="arm-000-r0",
                claim="c",
                self_report=True,
                oracle=1.0,
                rounds=1,
                attempts=1,
                reverted=False,
                stagnant=False,
                delivered_matches_verified=state,
            )
        )[4]
        for state in (True, False, None)
    }
    assert len({levels[True], levels[False], levels[None]}) == 3
    assert levels[None] != levels[False]
