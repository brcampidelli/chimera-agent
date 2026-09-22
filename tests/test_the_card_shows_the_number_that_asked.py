"""The number that raised the question reaches the card, and the answer becomes a label.

Study 20 §2.6, item 3 of the shortlist: the approval stream existed and was durable, and the line it
wrote held an action, a reason and an outcome — so "the person said yes in 8 s" could not be put
beside "and the probability that asked was 0.80", which is the only pairing that turns an answer into
a LABEL. The map comes from 55 bench items today; every card answered is one real row for a map
refitted on the deployment's own data.

What is held here, each with the failure it is against:

* the band and the build travel ON the verdict, not only on the audit line — the card is handed a
  `Verdict` and nothing else, so a number that lived only in the audit could not be rendered;
* `_facts_of` reads the number off the verdict the kernel already built, rather than a second
  argument a caller would eventually forget to forward;
* the record writes `p` as a NUMBER. This is the one column a refit reads, and a map fitted on
  ``"0.8"`` is a map fitted on nothing;
* a rule-raised question writes no `p` at all — ``0.0`` would read as a very confident ALLOW that a
  person nonetheless had to answer;
* the number goes on the QUESTION file too, so a card that mounts late — a reload, a second client —
  shows the same number the first one did.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.config import Settings
from chimera.decisions import CalibrationMaps, Decider, PlattMap, Reading
from chimera.decisions.governance import DANGER
from chimera.decisions.local import LocalLogprobBackend
from chimera.decisions.maps import SHIPPED_MAPS
from chimera.governance import pending
from chimera.governance.approval import _facts_of, ask_elsewhere
from chimera.governance.band import DecisionBand
from chimera.governance.kernel import TrustKernel
from chimera.governance.policy import Decision, Verdict

RESOLVED = "qwen3:4b@Q4_K_M"
ACTION = "python -c 'import shutil; shutil.rmtree(\"/home/bruno\")'"


class _Backend:
    """A backend that answers from a script of raw probabilities."""

    name = "local_logprob"
    model = "qwen3:4b"

    def __init__(self, raw: list[float]) -> None:
        self.raw = list(raw)
        self._instrument = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b").instrument(DANGER)

    def instrument(self, question: Any) -> str:
        return self._instrument

    def ask(self, state: str, question: Any) -> Reading:
        nxt = self.raw.pop(0)
        return Reading(
            choice="BLOCK" if nxt >= 0.5 else "ALLOW", shares=None, p=nxt, mass=0.99,
            logprobs_came=True, resolved_model=RESOLVED,
        )


def _identity_map() -> CalibrationMaps:
    """A map that leaves p alone, so the script's raw values ARE the calibrated ones."""
    shipped = SHIPPED_MAPS[0]
    return CalibrationMaps([PlattMap(**{**shipped.to_dict(), "id": "identity", "a": 1.0, "b": 0.0})])


def _band(raw: list[float]) -> DecisionBand:
    return DecisionBand(Decider(_Backend(raw), _identity_map()))


def _lines(home: Path) -> list[dict[str, Any]]:
    path = home / "approvals" / pending.HISTORY
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- the number on the verdict -------------------------------------------------------------------


def test_the_band_and_the_build_travel_on_the_verdict_the_card_is_handed() -> None:
    """The card receives a `Verdict` and nothing else (`governed_tool.py`). A number that lived only
    on the audit line could not be rendered — the card would show the reason and no number, which is
    the defect this item exists to fix."""
    verdict = TrustKernel(band=_band([0.80])).evaluate(ACTION)
    assert verdict.decision is Decision.REVIEW
    assert verdict.confidence == 0.80
    assert verdict.band == "review"
    assert verdict.decider_model == RESOLVED


def test_a_prior_carries_its_band_too() -> None:
    """Between the thresholds nothing is decided on the number — but a screen that shows it must be
    able to say which band it fell in, or `0.45` reads as a verdict it never was."""
    verdict = TrustKernel(band=_band([0.42])).evaluate(ACTION)
    assert verdict.decision is Decision.ALLOW and verdict.rule == "default"
    assert verdict.band == "uncertain" and verdict.decider_model == RESOLVED


def test_a_rule_raised_verdict_has_no_band_and_no_model() -> None:
    """A regex has no opinion about its own odds, and a card must not show it one."""
    verdict = TrustKernel(band=_band([])).evaluate("rm -rf /")
    assert verdict.decision is Decision.BLOCK
    assert verdict.band == "" and verdict.decider_model == "" and verdict.confidence is None


# --- the number read off the verdict -------------------------------------------------------------


def test_the_facts_read_the_number_off_the_verdict_rather_than_a_second_argument() -> None:
    """One source, not two. A caller that had to remember to forward `p` separately would eventually
    forget, and the record would then disagree with the card about the same question."""
    verdict = TrustKernel(band=_band([0.80])).evaluate(ACTION)
    facts = _facts_of(verdict, ACTION)
    assert facts["p"] == 0.80 and facts["band"] == "review" and facts["decider_model"] == RESOLVED


def test_a_rule_raises_no_number_in_the_facts() -> None:
    facts = _facts_of(Verdict(Decision.REVIEW, "plausibly dangerous", "curl-pipe-sh"), "run_shell\ncurl x | sh")
    assert facts == {"tool": "run_shell", "rule": "curl-pipe-sh"}


# --- the record ----------------------------------------------------------------------------------


def test_the_answer_keeps_the_number_beside_the_verdict_the_person_gave(tmp_path: Path) -> None:
    """The join. `p` beside `outcome` is what makes a line a label; without it the stream measures
    compliance and nothing else."""

    def answer_at_once(question: Any) -> None:
        pending.answer(tmp_path, question.id, True)

    ask = ask_elsewhere(tmp_path, on_asked=answer_at_once, wait_seconds=5.0, facts={"run_id": "turn-7"})
    assert ask(TrustKernel(band=_band([0.80])).evaluate(ACTION), ACTION) is True
    (line,) = _lines(tmp_path)
    assert line["outcome"] == "approved" and line["run_id"] == "turn-7"
    assert line["p"] == 0.80 and line["band"] == "review" and line["decider_model"] == RESOLVED


def test_the_number_is_written_as_a_number(tmp_path: Path) -> None:
    """The one column a refit reads. `str(0.8)` would make it ``"0.8"``, and a map fitted on strings
    is a map fitted on nothing — the same defect as a schema assumed instead of read (study 21 §2ad)."""
    pending.ask_durably(tmp_path, ACTION, "why", wait_seconds=0.0, p=0.8, band="review", decider_model=RESOLVED)
    (line,) = _lines(tmp_path)
    assert isinstance(line["p"], float) and line["p"] == 0.8
    assert json.loads(json.dumps(line))["p"] == 0.8  # survives the round trip it is stored as


def test_a_question_with_no_number_writes_no_column(tmp_path: Path) -> None:
    """A rule-raised question has no probability, and a `0.0` column would read as a very confident
    ALLOW that a person nonetheless had to answer."""
    pending.ask_durably(tmp_path, "run_shell\nls", "why", wait_seconds=0.0, facts={"run_id": "t"})
    (line,) = _lines(tmp_path)
    assert "p" not in line and "band" not in line and "decider_model" not in line


def test_a_timed_out_question_records_the_number_it_asked_with(tmp_path: Path) -> None:
    """A timeout is a real answer — the one that says nobody was reachable — and the number that
    went unanswered is the other half of "this band costs 6 benign refusals in 31"."""
    pending.ask_durably(tmp_path, ACTION, "why", wait_seconds=0.0, p=0.51, band="review", decider_model=RESOLVED)
    (line,) = _lines(tmp_path)
    assert line["outcome"] == "timeout" and line["p"] == 0.51 and line["band"] == "review"


# --- the question file ---------------------------------------------------------------------------


def test_the_number_goes_on_the_question_so_a_late_card_shows_the_same_one(tmp_path: Path) -> None:
    """`GET /api/approvals` reads the question files, not the announcement. A number that lived only
    in the live announcement would be missing exactly when the first screen was not the one that
    answered — a reload, a second window."""
    pending.ask_durably(tmp_path, ACTION, "why", wait_seconds=0.0, p=0.80, band="review", decider_model=RESOLVED)
    # The question is cleaned up when it resolves; write one by hand and read it back, which is what
    # the route does.
    directory = tmp_path / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "abc123.ask.json").write_text(
        json.dumps({"id": "abc123", "action": ACTION, "reason": "why", "asked_at": 1.0,
                    "decision": "review", "p": 0.80, "band": "review", "decider_model": RESOLVED}),
        encoding="utf-8",
    )
    (listed,) = pending.pending(tmp_path)
    assert listed.p == 0.80 and listed.band == "review" and listed.decider_model == RESOLVED


def test_a_question_asked_before_this_field_existed_reads_back_without_a_number(tmp_path: Path) -> None:
    """`None` is the honest answer for an old question, not a guessed zero."""
    directory = tmp_path / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "old.ask.json").write_text(
        json.dumps({"id": "old", "action": "run_shell\nls", "reason": "r", "asked_at": 1.0, "decision": "review"}),
        encoding="utf-8",
    )
    (listed,) = pending.pending(tmp_path)
    assert listed.p is None and listed.band == "" and listed.decider_model == ""


def test_the_announcement_carries_the_number_to_the_card(tmp_path: Path) -> None:
    """The live path: the screen renders the number on the frame it was handed, before any reload."""
    seen: list[Any] = []
    ask = ask_elsewhere(tmp_path, on_asked=seen.append, wait_seconds=0.0)
    ask(TrustKernel(band=_band([0.80])).evaluate(ACTION), ACTION)
    (question,) = seen
    assert question.p == 0.80 and question.band == "review" and question.decider_model == RESOLVED


# --- the wire ------------------------------------------------------------------------------------


def test_the_route_that_lists_a_reloaded_card_sends_the_number(tmp_path: Path, monkeypatch: Any) -> None:
    """`ApprovalOut` grew three fields and the handler has to fill them. A schema the handler does
    not fill is a field the client types as present and receives as null forever."""
    from chimera.api.schemas import ApprovalOut

    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    directory = home / "approvals"
    directory.mkdir(parents=True)
    (directory / "abc123.ask.json").write_text(
        json.dumps({"id": "abc123", "action": ACTION, "reason": "why", "asked_at": 1.0,
                    "decision": "review", "p": 0.80, "band": "review", "decider_model": RESOLVED}),
        encoding="utf-8",
    )
    Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    out = ApprovalOut(
        id="abc123", action=ACTION, reason="why", asked_at=1.0, age_seconds=0.0, decision="review",
        p=0.80, band="review", decider_model=RESOLVED,
    )
    dumped = out.model_dump()
    assert dumped["p"] == 0.80 and dumped["band"] == "review" and dumped["decider_model"] == RESOLVED
