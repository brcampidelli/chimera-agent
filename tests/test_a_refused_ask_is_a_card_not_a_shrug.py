"""A decision the gate refused fails toward scrutiny: a card, where an ordinary halt goes on.

Study 27, phase 3. The owner decided on 2026-09-29 that when the spend/rate gate refuses a hosted ask
the REVIEW band raises a card (`gate: budget` on the receipt) instead of letting the action fall to the
default. The two halts are different facts: a model that was down said nothing about the action, and a
meter that ran out is a reason not to wave anything through. This file holds both halves, because the
second half is the one a careless change would flip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.decisions import CalibrationMaps, Decider, Reading
from chimera.decisions.gate import GateRefused
from chimera.decisions.governance import DANGER
from chimera.decisions.local import LocalLogprobBackend
from chimera.governance.audit import AuditLog
from chimera.governance.band import DecisionBand
from chimera.governance.kernel import TrustKernel
from chimera.governance.policy import Decision


class _Backend:
    name = "openrouter_decisions"
    model = "typesafe/jev-1.13"

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self._instrument = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b").instrument(DANGER)

    def instrument(self, question: Any) -> str:
        return self._instrument

    def ask(self, state: str, question: Any) -> Reading:
        if self.error is not None:
            raise self.error
        return Reading(choice="ALLOW", shares=None, p=0.05)


def _band(error: Exception | None) -> DecisionBand:
    return DecisionBand(Decider(_Backend(error), CalibrationMaps([])))


def _last(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def test_a_gate_refusal_is_a_review_that_says_nothing_judged_the_action() -> None:
    reading = _band(GateRefused("budget", "daily decision ceiling reached")).read("make test")

    assert reading.band == "gate" and reading.verdict is not None
    assert reading.verdict.decision is Decision.REVIEW and reading.verdict.rule == "decision_band"
    assert "budget" in reading.verdict.reason and "nothing judged it" in reading.verdict.reason
    assert reading.p is None and reading.verdict.confidence is None  # there was no number to show


def test_the_rate_gate_is_a_review_too_and_says_which_meter() -> None:
    reading = _band(GateRefused("rate", "the wait exceeds 30s")).read("make test")

    assert reading.verdict is not None and "rate" in reading.verdict.reason


def test_an_ordinary_halt_still_goes_on_to_the_default() -> None:
    reading = _band(ConnectionRefusedError("nothing answered")).read("make test")

    assert reading.band == "halt" and reading.verdict is None


def test_through_the_kernel_a_gated_action_asks_and_an_unavailable_model_does_not(tmp_path: Path) -> None:
    gated = TrustKernel(audit=AuditLog(tmp_path / "gated.jsonl"), band=_band(GateRefused("budget", "x")))
    down = TrustKernel(audit=AuditLog(tmp_path / "down.jsonl"), band=_band(ConnectionRefusedError("down")))

    asked = gated.evaluate("make test")
    shrugged = down.evaluate("make test")

    assert asked.decision is Decision.REVIEW and asked.band == "gate" and asked.rule == "decision_band"
    assert shrugged.decision is Decision.ALLOW and shrugged.rule == "default"


def test_the_audit_line_names_the_gate_and_the_halt(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")

    TrustKernel(audit=audit, band=_band(GateRefused("budget", "daily ceiling"))).evaluate("make test")

    line = _last(audit.path)
    assert line["decision"] == "review" and line["band"] == "gate" and line["gate"] == "budget"
    assert line["decider_halt"].startswith("GateRefused") and "confidence" not in line


def test_an_ordinary_halt_names_no_gate_on_the_audit_line(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")

    TrustKernel(audit=audit, band=_band(ConnectionRefusedError("down"))).evaluate("make test")

    assert "gate" not in _last(audit.path) and _last(audit.path)["band"] == "halt"


def test_a_rule_still_beats_the_band_so_a_gated_meter_never_delays_a_fixed_signature(tmp_path: Path) -> None:
    # The default rule set already blocks `rm -rf /`; the band is consulted only where no rule matched.
    kernel = TrustKernel(audit=AuditLog(tmp_path / "a.jsonl"), band=_band(GateRefused("budget", "x")))

    verdict = kernel.evaluate("rm -rf /")

    assert verdict.decision is Decision.BLOCK and verdict.rule != "decision_band"
