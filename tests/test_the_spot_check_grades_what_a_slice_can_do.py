"""The spot check runs on one path — a summary that is `_distill`'s head + tail slice of the raw
output — and on that path two of its three checks could not fail for a true reason: a verbatim slice
invents nothing and contradicts nothing. `bench/blind_audit` (2026-09-11) recorded what asking for
them cost: when the shipped auditor failed an item it marked INVENTED and CONTRADICTION as often as
DROPPED. The prompt now says what the summary is and grades the one thing a slice can do — omit.
The three-check prompt stays under its own name so the bench's `shipped` arm reproduces its number.
Fakes only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.envelope_verify import (
    _SPOT_SYSTEM,
    _SPOT_SYSTEM_DROPPED_ONLY,
    _SPOT_SYSTEM_THREE_CHECKS,
    EnvelopeVerifier,
    _grade_faithfulness,
)
from chimera.orchestration.spec import TaskSpec
from chimera.providers import CompletionResult

PLANT = "ESCALATION REQUIRED: PM-4 wrote full card numbers to the debug log for 9 days."


class _Auditor:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.systems: list[str] = []

    def complete(self, messages: Any, **kwargs: Any) -> CompletionResult:
        first = messages[0]
        self.systems.append(first["content"] if isinstance(first, dict) else first.content)
        return CompletionResult(content=self.reply, model="auditor")


def _long_raw() -> str:
    filler = "Section text about an ordinary postmortem with owners and statuses.\n\n" * 90
    return "Summary of findings: six postmortems reviewed.\n\n" + filler + PLANT + "\n\n" + filler


def _spec() -> TaskSpec:
    return TaskSpec(task_id="pm", objective="Review the postmortems and report anything that needs escalation.")


def _spot(tmp_path: Path, reply: str, **kw: Any) -> tuple[Any, _Auditor]:
    store = ArtifactStore(tmp_path)
    envelope = build_envelope(_spec(), _long_raw(), store)
    assert PLANT not in envelope.summary and envelope.evidence_refs
    auditor = _Auditor(reply)
    verifier = EnvelopeVerifier(store=store, backend=auditor, model="auditor", spot_rate=1.0,
                                recover_dropped=False, **kw)
    return verifier.verify(_spec(), envelope, force_spot=True), auditor


def test_the_shipped_prompt_grades_dropped_and_names_neither_of_the_other_two() -> None:
    assert _SPOT_SYSTEM is _SPOT_SYSTEM_DROPPED_ONLY
    assert "DROPPED" in _SPOT_SYSTEM
    assert "INVENTED" not in _SPOT_SYSTEM and "CONTRADICTION" not in _SPOT_SYSTEM
    # And it tells the auditor what it is holding, which is the reason the two checks are gone.
    assert "slice" in _SPOT_SYSTEM and "cannot invent" in _SPOT_SYSTEM


def test_the_verifier_sends_the_dropped_only_prompt_by_default(tmp_path: Path) -> None:
    outcome, auditor = _spot(tmp_path, "DROPPED: PASS\nNothing critical was cut.")
    assert outcome.passed and outcome.stage == "spot"
    assert auditor.systems == [_SPOT_SYSTEM_DROPPED_ONLY]


def test_a_dropped_fail_still_fails_and_escalates(tmp_path: Path) -> None:
    outcome, _ = _spot(tmp_path, f"DROPPED: FAIL\nThe summary omits: {PLANT}")
    assert outcome.passed is False and outcome.stage == "spot" and outcome.escalate is True


def test_the_three_check_prompt_is_kept_and_selectable_for_the_bench(tmp_path: Path) -> None:
    assert "INVENTED: PASS|FAIL" in _SPOT_SYSTEM_THREE_CHECKS
    outcome, auditor = _spot(
        tmp_path, "INVENTED: PASS\nDROPPED: PASS\nCONTRADICTION: PASS\nFaithful.",
        spot_system=_SPOT_SYSTEM_THREE_CHECKS,
    )
    assert outcome.passed and auditor.systems == [_SPOT_SYSTEM_THREE_CHECKS]


def test_the_grader_reads_the_one_line_reply_and_the_three_line_one_alike() -> None:
    assert _grade_faithfulness("DROPPED: PASS\nNothing critical was cut.") is True
    assert _grade_faithfulness("DROPPED: FAIL\nThe escalation was cut.") is False
    assert _grade_faithfulness("INVENTED: PASS\nDROPPED: FAIL\nCONTRADICTION: PASS") is False
