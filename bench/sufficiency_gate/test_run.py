"""Wiring of the sufficiency-gate replay, on the frozen verified_cascade run and a fake gate.

These check that the arms are the counterfactuals the registration says they are; they are not
measurement evidence (no model is called).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.sufficiency_gate import run as sg
from bench.verified_cascade.replay import RunData


@pytest.fixture(scope="module")
def rd() -> RunData:
    return RunData(sg.VC_RUN)


def test_a_gate_that_passes_everything_is_the_no_gate_arm_plus_one_local_read(rd: RunData) -> None:
    for item in rd.items[:60]:
        rows = sg.item_rows(rd, item, {"choice": "sufficient", "p": 1.0}, sg.RULES["argmax"])
        assert rows["C"].label == rows["A"].label
        assert (rows["C"].gen_calls, rows["C"].local_calls) == (1, 1)
        assert rows["C+D"].label == rows["D"].label
        assert rows["C+D"].gen_calls == rows["D"].gen_calls
        assert rows["C+D"].local_calls == rows["D"].local_calls + 1


def test_a_gate_that_blocks_everything_declines_without_calling_the_generator(rd: RunData) -> None:
    for item in rd.items[:60]:
        rows = sg.item_rows(rd, item, {"choice": "insufficient", "p": 0.0}, sg.RULES["argmax"])
        for arm in ("C", "C+D"):
            assert rows[arm].abstained and rows[arm].gen_calls == 0 and rows[arm].local_calls == 1
            assert rows[arm].label == ("declined" if item["family"] == "ANS" else "correct")
            assert not rows[arm].wrong


def test_the_primary_rule_is_the_argmax_not_a_threshold_on_p() -> None:
    # A choice the backend made and a p below one half disagree only when the shares are not the
    # two options alone; the rule that decides is the choice (§2af).
    assert sg.RULES["argmax"]({"choice": "sufficient", "p": 0.4})
    assert not sg.RULES["argmax"]({"choice": "insufficient", "p": 0.6})


def test_the_report_reproduces_the_published_replay_and_refuses_a_different_one(
    rd: RunData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sg.collect(tmp_path, sg.FakeGate(), limit=12, floor=4, rd=rd)
    rep = sg.build(tmp_path, rd)
    assert rep["control_replay"]["A"]["wrong"] == 33
    assert rep["control_replay"]["D_decl"] == {"wrong": 21, "n": 398, "published": [21, 398]}
    assert rep["gate_reads"] == 12 and rep["floor"]["n"] == 4 and rep["floor"]["flips"] == 0
    arms = rep["rules"]["argmax"]["arms"]
    assert arms["ALL-DECLINE"]["wrong"] == 0 and arms["ALL-DECLINE"]["coverage_ans_answered"] == 0
    json.dumps(rep)  # serialisable as written to report.json
    monkeypatch.setattr(sg, "PUBLISHED", {"A": (34, 400), "D_decl": (21, 398)})
    with pytest.raises(SystemExit, match="HALT"):
        sg.build(tmp_path, rd)


def test_the_verdict_refuses_a_win_bought_with_coverage() -> None:
    d = {"wrong": 21, "coverage_ans_answered": 144, "gen_calls": 414}
    fewer_answers = {"wrong": 10, "coverage_ans_answered": 140, "gen_calls": 300}
    assert sg._verdict(fewer_answers, d)["wins"] is False
    same_answers = {"wrong": 21, "coverage_ans_answered": 144, "gen_calls": 300}
    assert sg._verdict(same_answers, d)["wins"] is True
    no_saving = {"wrong": 20, "coverage_ans_answered": 144, "gen_calls": 414}
    assert sg._verdict(no_saving, d)["wins"] is False
