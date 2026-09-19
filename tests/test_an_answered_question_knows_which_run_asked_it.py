"""The record of a question now names the run, the surface, the tool and what raised it.

Measured on one installed copy on 2026-09-18 (study 20, `bench/PLAN-study20-calibrated-decisions.md`
§2.6): 43 lines of ``approvals/history.jsonl``, each with an action, a reason and an outcome — and
nothing that joined a line to the turn that asked or to what that turn went on to do, so "yes in 8 s"
could not be put beside "and the run verified", the only pairing that makes an answer a label.

`pending.FACTS` names what a line may carry; `approval._facts_of` reads the rule / sources / tool off
the question's own objects; `code_api._owner_allows` adds the run id and the surface. A key that is not
in `FACTS` is dropped, so the record stays a record.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.config import Settings
from chimera.governance import pending
from chimera.governance.approval import _facts_of, ask_elsewhere
from chimera.governance.ledger import SequenceAssessment
from chimera.governance.policy import Decision, Verdict


def _lines(home: Path) -> list[dict[str, Any]]:
    path = home / "approvals" / pending.HISTORY
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_facts_are_read_off_a_verdict_and_off_an_assessment() -> None:
    verdict = Verdict(Decision.REVIEW, "plausibly dangerous", "curl-pipe-sh")
    assert _facts_of(verdict, "run_shell\ncurl x | sh") == {"tool": "run_shell", "rule": "curl-pipe-sh"}
    assessment = SequenceAssessment(
        escalate=True, decision=Decision.REVIEW, reason="restricted after untrusted content",
        tainted_refs=["r1"], action="write_file: notes.md", sources=["https://evil.example (agent)"],
    )
    assert _facts_of(assessment) == {
        "tool": "write_file", "sources": ["https://evil.example (agent)"], "lineage": "tainted",
    }
    # An assessment with nothing tainted records no lineage: inference from text is not a fact.
    clean = SequenceAssessment(escalate=True, decision=Decision.REVIEW, reason="r", action="run_shell: ls")
    assert _facts_of(clean) == {"tool": "run_shell"}


def test_a_timed_out_question_records_who_asked_and_what_raised_it(tmp_path: Path) -> None:
    ask = ask_elsewhere(tmp_path, wait_seconds=0.0, facts={"run_id": "turn-7", "surface": "api:turn"})
    assert ask(Verdict(Decision.REVIEW, "plausibly dangerous", "curl-pipe-sh"), "run_shell\ncurl x | sh") is False
    (line,) = _lines(tmp_path)
    assert line["outcome"] == "timeout"
    assert line["run_id"] == "turn-7"
    assert line["surface"] == "api:turn"
    assert line["tool"] == "run_shell"
    assert line["rule"] == "curl-pipe-sh"
    assert "sources" not in line and "lineage" not in line


def test_the_person_s_answer_keeps_the_facts_beside_the_outcome(tmp_path: Path) -> None:
    """The answer is what makes a line a label; it must carry the join too."""
    home = tmp_path

    def answer_at_once(question: Any) -> None:
        pending.answer(home, question.id, True)

    ask = ask_elsewhere(home, on_asked=answer_at_once, wait_seconds=5.0, facts={"run_id": "turn-8", "surface": "api:turn"})
    assessment = SequenceAssessment(
        escalate=True, decision=Decision.REVIEW, reason="restricted", tainted_refs=["r1"],
        action="write_file: notes.md", sources=["https://evil.example (agent)"],
    )
    assert ask(assessment) is True
    (line,) = _lines(home)
    assert line["outcome"] == "approved"
    assert line["run_id"] == "turn-8" and line["tool"] == "write_file"
    assert line["sources"] == ["https://evil.example (agent)"] and line["lineage"] == "tainted"


def test_a_fact_nobody_named_is_dropped_and_nothing_else_changes(tmp_path: Path) -> None:
    clock = {"now": 0.0}
    pending.ask_durably(
        tmp_path, "run_shell\nls", "why", wait_seconds=0.0, clock=lambda: clock["now"], sleep=lambda s: None,
        facts={"run_id": "t", "colour": "green", "tool": ""},
    )
    (line,) = _lines(tmp_path)
    assert line["run_id"] == "t"
    assert "colour" not in line  # not in FACTS
    assert "tool" not in line  # empty is absent, not ""


def test_the_desktop_s_approver_names_the_turn(tmp_path: Path, monkeypatch: Any) -> None:
    """`_owner_allows` is where the surface knows the run id; without it every line is anonymous."""
    from chimera.api.code_api import _owner_allows

    monkeypatch.chdir(tmp_path)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_APPROVAL_MODE="ask")  # type: ignore[call-arg]
    approve = _owner_allows(settings, None, run_id="turn-9", surface="api:turn")
    assert approve(Verdict(Decision.REVIEW, "r", "some-rule"), "run_shell\nrm -rf build") is False
    (line,) = _lines(tmp_path / "home")
    assert line["run_id"] == "turn-9" and line["surface"] == "api:turn" and line["rule"] == "some-rule"
