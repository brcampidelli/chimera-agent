"""`bench/verified_cascade/controls.py` — the content-blind controls and the leave-one-out that
`PREREGISTRATION-controls.md` registers for the shipped default (study 30, S30-33).

Pinned on cases whose answer is known by construction, and on the run itself: D's own actions,
pushed through the control's machinery, must reproduce what the registered replay says D shipped —
otherwise the controls would permute some other policy and still print a plausible number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.verified_cascade import controls  # noqa: E402
from bench.verified_cascade.controls import DIVERT, ESCALATE, KEEP, NOT_ANSWERED, Item  # noqa: E402

RUN = REPO / "bench" / "verified_cascade" / "results" / "run"
# The run's rows are committed; a checkout that leaves `results/` out (the WSL gate copies without it)
# has nothing to replay, and that says nothing about the code. Skipped there, run everywhere else.
needs_run = pytest.mark.skipif(not (RUN / "calls.jsonl").exists(), reason="results/run is not in this checkout")


def _item(n: int, *, family: str = "NCR", a: str = "correct", d: str = "correct", action: str = KEEP,
          esc: str = NOT_ANSWERED, doc: str = "x", lang: str = "en") -> Item:
    return Item(f"i{n}", family, doc, lang, a, d, action, esc)


def _clairvoyant() -> list[Item]:
    """20 wrong drafts among 100: D diverts exactly the 20 wrong ones and keeps the rest."""
    wrong = [_item(i, a="wrong", d="handoff", action=DIVERT) for i in range(20)]
    right = [_item(20 + i, family="ANS", action=KEEP) for i in range(80)]
    return wrong + right


def test_a_verifier_that_diverts_exactly_the_wrong_drafts_beats_random_placement() -> None:
    rep = controls.control_r2(_clairvoyant(), draws=300)
    assert rep["wrong"]["observed"] == 0 and rep["wrong"]["below_p5"]
    assert rep["ans_not_answered"]["observed"] == 0 and rep["ans_not_answered"]["below_p5"]


def test_a_verifier_that_diverts_at_random_does_not() -> None:
    items = _clairvoyant()
    # Same 20 diversions, but placed on the first 20 right drafts instead of the wrong ones.
    blind = [Item(i.item_id, i.family, i.doc, i.lang, i.a_label,
                  ("handoff" if 20 <= n < 40 else i.a_label), (DIVERT if 20 <= n < 40 else KEEP), i.esc_label)
             for n, i in enumerate(items)]
    rep = controls.control_r2(blind, draws=300)
    assert rep["wrong"]["observed"] == 20 and not rep["wrong"]["below_p5"]


def test_r1_moves_only_as_many_items_as_d_escalated() -> None:
    items = [_item(i, a="wrong", d="wrong") for i in range(10)] + [_item(10, a="wrong", d="correct", action=ESCALATE, esc="correct")]
    rep = controls.control_r1(items, draws=200)
    assert rep["k_escalated"] == 1
    # One random escalation among 11 wrong drafts can fix at most one of them.
    assert rep["wrong"]["min"] >= 10 and rep["wrong"]["max"] <= 11


def test_leave_one_out_flags_a_category_whose_removal_flips_the_sign() -> None:
    helped = [_item(i, doc="good", a="wrong", d="handoff", action=DIVERT) for i in range(6)]
    hurt = [_item(10 + i, doc="bad", a="correct", d="wrong", action=ESCALATE, esc="wrong") for i in range(2)]
    loo = controls.leave_one_out(helped + hurt, "doc")
    assert loo["good"]["sign_lost"] and not loo["bad"]["sign_lost"]
    assert loo["bad"]["carried_by_this"] is False and loo["good"]["carried_by_this"] is True


@needs_run
def test_d_s_actions_reproduce_the_registered_replay_on_the_run() -> None:
    from bench.verified_cascade.replay import RunData

    items = controls.build_items(RunData(RUN))
    assert items and controls.replay_mismatches(items) == 0
    assert {i.action for i in items} == {KEEP, ESCALATE, DIVERT}


@needs_run
def test_the_published_controls_are_what_the_script_computes_now() -> None:
    assert json.loads((RUN / "controls.json").read_text(encoding="utf-8")) == json.loads(
        json.dumps(controls.build(RUN), sort_keys=True))


def test_the_report_names_what_the_registered_set_leaves_out() -> None:
    full = [_item(0, d="wrong", a="wrong"), _item(1), _item(2, action=DIVERT, d="handoff")]
    rep = controls.excluded_from_p(full[1:], full)
    assert rep["n"] == 1 and rep["by_action"][KEEP] == 1 and rep["d_wrong"] == 1 and rep["items"] == ["i0"]


@needs_run
def test_the_full_set_puts_back_the_d_keeps_the_registered_set_drops() -> None:
    """P's cut is conditioned on D keeping d1: on this run all 11 items it drops are D-keeps and 5 of
    them are D-wrong. The full-set reading must carry them, or it reports P a second time."""
    from bench.verified_cascade.replay import RunData

    rd = RunData(RUN)
    registered = controls.build_items(rd)
    sens = controls.sensitivity_full_set(rd, registered)
    ex = sens["excluded_from_P"]
    assert ex["n"] == 11 and ex["by_action"] == {KEEP: 11, ESCALATE: 0, DIVERT: 0} and ex["d_wrong"] == 5
    for fill, r in sens["by_fill"].items():
        assert r["n"] == len(registered) + 11, fill
        assert r["all"]["wrong_d"] == 21 and r["all"]["wrong_a"] == 32
        assert not r["R2"]["wrong"]["below_p5"] and r["R1"]["wrong"]["below_p5"]
