"""The verified-cascade policies (§4.2) and gates (§5.3, §9), on synthetic inputs, with no network.

* **B and D route as registered:** supported at p >= 0.8 ships the draft; ``declined`` hands off (or
  ships the decline, the secondary variant); anything else escalates to Sol, which ships only if it is
  itself supported at 0.8. A missing call leaves the item out, never counted as a pass.
* **The threshold is read together with the choice**: p >= 0.8 with another option written is not an
  acceptance (lessons §2af: a confidence is read against the rule that decides).
* **L is the shipped gate code**: a refusal marker fails ``default_gate``, two unlike answers find no
  majority and escalate.
* **The number check is first and final** in a label, then agreement, then a human.
* **The instrument gate** drops a verifier accepting > 90% of unsupported constructions or < 50% of
  V-gold; **gate G** needs agreement 90%, recall 85% on the wrong constructions, <= 5% wrong on gold.
* **The base-rate gate** stops at W < 15, at a wrong rate over 40%, while adjudications are pending,
  and when adjudications exceed 15%.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.verified_cascade import harness, run  # noqa: E402
from bench.verified_cascade.harness import Reading, final_label, lexical, verified  # noqa: E402


def test_supported_above_the_threshold_ships_the_draft() -> None:
    out = verified("d1", Reading("supported", 0.85), None, "jev", has_f1=False)
    assert out.shipped == "d1" and not out.escalated and out.calls == ("d1", "jev:d1")


def test_the_threshold_is_read_with_the_choice() -> None:
    assert not Reading("unsupported", 0.9).accepts(0.8)
    assert not Reading("supported", 0.79).accepts(0.8)
    out = verified("d1", Reading("supported", 0.79), Reading("supported", 0.95), "jev", has_f1=True)
    assert out.shipped == "f1" and out.escalated


def test_declined_hands_off_or_ships_the_decline_in_the_secondary_variant() -> None:
    assert verified("d1", Reading("declined", 0.02), None, "jev", has_f1=False).shipped == "handoff"
    assert verified("d1", Reading("declined", 0.02), None, "jev", has_f1=False, declined_ships=True).shipped == "decline"


def test_an_escalation_that_fails_its_own_read_is_a_handoff() -> None:
    out = verified("d1", Reading("unsupported", 0.1), Reading("unsupported", 0.2), "local", has_f1=True)
    assert out.shipped == "handoff" and out.calls == ("d1", "local:d1", "f1", "local:f1")


def test_a_missing_call_leaves_the_item_out() -> None:
    assert verified("d1", None, None, "jev", has_f1=False).shipped is None
    assert verified("d1", Reading("unsupported", 0.1), None, "jev", has_f1=False).shipped is None


def test_the_lexical_arm_runs_the_shipped_gate_code() -> None:
    same = "The flag is --max-usd and it caps the whole session."
    out, info = lexical(same, same, None)
    assert out.shipped in ("d1", "d2") and info["majority"]
    refusal = "I can't answer that from the excerpts."
    out, info = lexical(refusal, refusal, "Sol says --max-usd.")
    assert info["majority"] and not info["gate_winner"] and out.shipped == "f1"
    out, info = lexical("Answer one about ports.", "Something entirely different here, about flags.", None)
    assert not info["majority"] and out.escalated and out.shipped is None


def test_the_number_check_is_first_and_final_then_agreement_then_a_human() -> None:
    assert final_label({"g1": "correct", "g2": "correct"}, None, True) == ("wrong", "number_check")
    assert final_label({"g1": "correct", "g2": "correct"}, None, False) == ("correct", "agreement")
    assert final_label({"g1": "correct", "g2": "wrong"}, None, False) == (None, "pending")
    assert final_label({"g1": "correct", "g2": "wrong"}, "wrong", False) == ("wrong", "adjudicated")


def test_a_grader_reply_is_read_as_one_json_label() -> None:
    assert harness.parse_label('{"label": "wrong"}') == "wrong"
    assert harness.parse_label('Sure. {"label": "Correct"}') == "correct"
    assert harness.parse_label('{"label": "maybe"}') is None
    assert harness.parse_label("") is None


def test_the_wall_refuses_tools_plugins_and_online() -> None:
    assert harness.wall_violations({"model": "m", "messages": []}) == []
    assert "tools" in harness.wall_violations({"model": "m", "tools": [{"type": "function"}]})
    assert "plugins" in harness.wall_violations({"model": "m", "extra_body": {"plugins": []}})
    assert ":online" in harness.wall_violations({"model": "openrouter/x:online"})


def _log(rows: dict[str, dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(get=rows.get)


def test_the_instrument_gate_drops_a_verifier_that_accepts_everything() -> None:
    vslice = [{"vid": f"g{i}", "kind": "gold"} for i in range(10)] + [{"vid": f"u{i}", "kind": "num"} for i in range(10)]
    everything = {f"read|jev|{v['vid']}": {"status": "ok", "choice": "supported", "p": 0.95} for v in vslice}
    h = SimpleNamespace(vslice=vslice, log=_log(everything))
    assert not run.instrument_gate(h, "jev")["passed"]
    sensible = {**everything, **{f"read|jev|u{i}": {"status": "ok", "choice": "unsupported", "p": 0.1} for i in range(10)}}
    h = SimpleNamespace(vslice=vslice, log=_log(sensible))
    assert run.instrument_gate(h, "jev")["passed"]
    nothing = {k: {"status": "ok", "choice": "unsupported", "p": 0.1} for k in everything}
    h = SimpleNamespace(vslice=vslice, log=_log(nothing))
    assert not run.instrument_gate(h, "jev")["passed"], "a verifier that accepts no V-gold must be dropped"


def test_gate_g_needs_agreement_recall_and_few_wrong_golds() -> None:
    sample = ([{"vid": f"g{i}", "kind": "gold", "label": "supported", "family": "ANS"} for i in range(20)]
              + [{"vid": f"w{i}", "kind": "offtopic", "label": "unsupported", "family": "ANS"} for i in range(20)]
              + [{"vid": f"d{i}", "kind": "decline", "label": "declined", "family": "NCR"} for i in range(10)])
    perfect = {f"grade|g1|{v['vid']}": {"status": "ok", "label": run.v_grading_label(v)} for v in sample}
    h = SimpleNamespace(preflight_sample=lambda: sample, log=_log(perfect))
    assert run.grader_gate(h, "g1")["passed"]
    assert run.v_grading_label(sample[-1]) == "correct", "a decline on an NCR item is the correct answer"
    harsh = {**perfect, **{f"grade|g1|g{i}": {"status": "ok", "label": "wrong"} for i in range(2)}}
    assert not run.grader_gate(SimpleNamespace(preflight_sample=lambda: sample, log=_log(harsh)), "g1")["passed"]


def _rd(labels: list[tuple[str | None, str]]) -> SimpleNamespace:
    it = iter(labels)
    return SimpleNamespace(label=lambda _iid, _draw: next(it))


def test_the_base_rate_gate() -> None:
    items = [{"item_id": str(i)} for i in range(100)]
    ok = [("wrong", "agreement")] * 20 + [("correct", "agreement")] * 80
    passed, d = run.base_rate_gate(SimpleNamespace(rd=_rd(ok)), items, False)
    assert passed and d["W"] == 20
    few = [("wrong", "agreement")] * 14 + [("correct", "agreement")] * 86
    assert not run.base_rate_gate(SimpleNamespace(rd=_rd(few)), items, False)[0]
    broken = [("wrong", "agreement")] * 41 + [("correct", "agreement")] * 59
    assert not run.base_rate_gate(SimpleNamespace(rd=_rd(broken)), items, False)[0]
    pending = [(None, "pending")] + ok[1:]
    passed, d = run.base_rate_gate(SimpleNamespace(rd=_rd(pending)), items, False)
    assert not passed and "adjudication" in d["note"]
    many_humans = [("wrong", "adjudicated")] * 16 + [("correct", "agreement")] * 84
    passed, d = run.base_rate_gate(SimpleNamespace(rd=_rd(many_humans)), items, False)
    assert not passed and "15%" in d["note"]


def test_the_drafting_prompt_and_decision_are_pinned() -> None:
    digest = harness.sha(harness.DRAFT_SYSTEM)
    assert digest == harness.DRAFT_SYSTEM_SHA
    assert harness.DRAFT_SYSTEM.startswith("You answer questions using only the excerpts")
    assert harness.OPTIONS == ("supported", "unsupported", "declined")
    q = harness.decision_question()
    assert q.options == harness.OPTIONS and q.event == ("supported",)
    state = harness.decision_state(["a", "b"], "q?", "ans")
    assert list(__import__("json").loads(state)) == ["excerpts", "question", "answer"]
