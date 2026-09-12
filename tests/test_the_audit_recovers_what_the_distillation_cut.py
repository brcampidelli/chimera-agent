"""The blind audit recovers the critical finding the distillation cut — and never rejects on it.

`build_envelope` keeps the first 70% and the last 15% of a long worker output. Whatever sat in the
middle is gone from the summary the synthesis reads, and the one-call spot check that was meant to
notice — summary + raw + "do not trust the summary" — said DROPPED on **4 of 23** such envelopes
(`bench/blind_audit`, 2026-09-11, the production weak-tier auditor). arXiv 2609.07680 named the
mechanism: an auditor handed the conclusion adopts it. A two-call form that reads the raw output
without the summary caught **19 of 23** — and flagged 11 of 23 summaries that dropped nothing, the
paper's stated cost. So the result is a RECOVERY: the absent critical findings are appended to the
summary, labelled, bounded; `passed` never moves on the audit's word.

The tests read the prompts the auditor received, because stage 1 must never see the summary and
stage 2 must never see the raw output — that separation is the whole mechanism.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.envelope_verify import EnvelopeVerifier, absent_critical
from chimera.orchestration.spec import TaskSpec
from chimera.providers import CompletionResult

PLANT = "ESCALATION REQUIRED: PM-4 wrote full card numbers to the debug log for 9 days."


class _Auditor:
    """Scripted replies per stage; keeps every prompt so the separation can be asserted."""

    def __init__(self, spot: str, findings: str, verdicts: str) -> None:
        self.replies = [spot, findings, verdicts]
        self.prompts: list[str] = []

    def complete(self, messages: Any, **kwargs: Any) -> CompletionResult:
        self.prompts.append(messages[-1]["content"] if isinstance(messages[-1], dict) else messages[-1].content)
        return CompletionResult(content=self.replies[len(self.prompts) - 1], model="auditor")


def _long_raw() -> str:
    head = "Summary of findings: six postmortems reviewed, four action items open.\n\n"
    filler = ("Section text about an ordinary postmortem with owners and statuses.\n\n" * 90)
    return head + filler + PLANT + "\n\n" + filler


def _spec() -> TaskSpec:
    return TaskSpec(task_id="pm", objective="Review the postmortems and report anything that needs escalation.")


def _verify(tmp_path: Path, auditor: _Auditor, **kw: Any) -> tuple[Any, Any]:
    store = ArtifactStore(tmp_path)
    envelope = build_envelope(_spec(), _long_raw(), store)
    assert PLANT not in envelope.summary and envelope.evidence_refs, "the plant must sit where _distill cuts"
    # `blind_audit=True` is the two-call form these tests are about; it is off by default since
    # `bench/blind_audit`'s addendum 2 measured it behind the one-call check (0 of 3 plants it
    # missed recovered, 11 of 23 clean summaries grown), and the default is asserted on its own below.
    if kw.pop("as_shipped", False):
        kw.pop("blind_audit", None)  # the constructor's own default, whatever it is
    else:
        kw.setdefault("blind_audit", True)
    verifier = EnvelopeVerifier(store=store, backend=auditor, model="auditor", spot_rate=1.0, **kw)
    return verifier.verify(_spec(), envelope, force_spot=True), envelope


def test_by_default_a_silent_pass_is_one_call_and_the_two_call_audit_does_not_run(tmp_path: Path) -> None:
    auditor = _Auditor(
        spot="DROPPED: PASS\nNothing critical was cut.",
        findings=f"1. Six postmortems reviewed\n2. [CRITICAL] {PLANT}",
        verdicts="1: PRESENT\n2: ABSENT\nDROPPED: FAIL",
    )
    outcome, _ = _verify(tmp_path, auditor, as_shipped=True)
    assert outcome.passed is True and outcome.recovered == ()
    assert len(auditor.prompts) == 1 and outcome.checks_run == ("schema", "spot")


def test_by_default_a_dropped_verdict_with_no_sentence_recovers_nothing_and_makes_no_second_call(tmp_path: Path) -> None:
    auditor = _Auditor(spot="DROPPED: FAIL", findings=f"1. [CRITICAL] {PLANT}", verdicts="1: ABSENT\nDROPPED: FAIL")
    outcome, _ = _verify(tmp_path, auditor, as_shipped=True)
    assert outcome.passed is True and outcome.recovered == () and len(auditor.prompts) == 1


def test_the_absent_critical_finding_is_recovered_and_the_verdict_does_not_move(tmp_path: Path) -> None:
    auditor = _Auditor(
        spot="INVENTED: PASS\nDROPPED: PASS\nCONTRADICTION: PASS\nThe summary is faithful.",
        findings="1. Six postmortems reviewed\n2. [CRITICAL] Full card numbers written to the debug log for 9 days (PM-4)\n3. Four action items open",
        verdicts="1: PRESENT\n2: ABSENT\n3: PRESENT\nDROPPED: FAIL",
    )
    outcome, envelope = _verify(tmp_path, auditor)
    assert outcome.passed is True, "the audit recovers; it does not reject"
    assert outcome.recovered == ("Full card numbers written to the debug log for 9 days (PM-4)",)
    assert outcome.checks_run == ("schema", "spot", "recover")
    # Stage 1 saw the raw output and NOT the summary's section header; stage 2 saw the summary and
    # NOT the raw output.
    spot, extract, compare = auditor.prompts
    assert "## Worker summary" in spot and "## Raw output" in spot
    assert "## Raw output" in extract and "## Worker summary" not in extract and "## Summary" not in extract
    assert "## Summary" in compare and "## Raw output" not in compare and PLANT not in compare


def test_nothing_critical_absent_means_nothing_recovered_and_no_second_call(tmp_path: Path) -> None:
    auditor = _Auditor(
        spot="INVENTED: PASS\nDROPPED: PASS\nCONTRADICTION: PASS\nfine",
        findings="1. Six postmortems reviewed\n2. Four action items open",  # no [CRITICAL]
        verdicts="unused",
    )
    outcome, _ = _verify(tmp_path, auditor)
    assert outcome.recovered == () and len(auditor.prompts) == 2


def test_a_dropped_verdict_that_names_the_omission_is_the_recovery_and_needs_no_second_stage(tmp_path: Path) -> None:
    auditor = _Auditor(spot=f"DROPPED: FAIL\nThe summary omits: {PLANT}", findings="", verdicts="")
    outcome, _ = _verify(tmp_path, auditor)
    assert outcome.passed is True and outcome.escalate is False
    assert outcome.recovered == (f"The summary omits: {PLANT}",)
    assert len(auditor.prompts) == 1 and outcome.checks_run == ("schema", "spot")


def test_a_dropped_verdict_with_no_sentence_falls_back_to_the_two_call_audit(tmp_path: Path) -> None:
    auditor = _Auditor(
        spot="DROPPED: FAIL",
        findings=f"1. Six postmortems reviewed\n2. [CRITICAL] {PLANT}",
        verdicts="1: PRESENT\n2: ABSENT\nDROPPED: FAIL",
    )
    outcome, _ = _verify(tmp_path, auditor)
    assert outcome.passed is True and outcome.recovered == (PLANT,)
    assert len(auditor.prompts) == 3 and outcome.checks_run == ("schema", "spot", "recover")


def test_with_the_recovery_off_a_dropped_verdict_is_the_gate_it_was(tmp_path: Path) -> None:
    auditor = _Auditor(spot=f"DROPPED: FAIL\nThe summary omits: {PLANT}", findings="", verdicts="")
    outcome, _ = _verify(tmp_path, auditor, recover_dropped=False)
    assert outcome.passed is False and outcome.escalate is True and outcome.recovered == ()
    assert len(auditor.prompts) == 1


def test_switched_off_the_verifier_is_the_one_call_it_was(tmp_path: Path) -> None:
    auditor = _Auditor(spot="INVENTED: PASS\nDROPPED: PASS\nCONTRADICTION: PASS\nfine", findings="", verdicts="")
    outcome, _ = _verify(tmp_path, auditor, recover_dropped=False)
    assert outcome.passed and outcome.recovered == () and len(auditor.prompts) == 1
    assert "recover" not in outcome.checks_run


def test_a_recovery_that_cannot_run_leaves_the_summary_as_it_was(tmp_path: Path) -> None:
    class _Flaky(_Auditor):
        def complete(self, messages: Any, **kwargs: Any) -> CompletionResult:
            if len(self.prompts) >= 1:
                raise RuntimeError("provider down")
            return super().complete(messages, **kwargs)

    auditor = _Flaky(spot="INVENTED: PASS\nDROPPED: PASS\nCONTRADICTION: PASS\nfine", findings="", verdicts="")
    outcome, _ = _verify(tmp_path, auditor)
    assert outcome.passed and outcome.recovered == ()


def test_absent_critical_is_bounded_ordered_and_only_critical() -> None:
    findings = "\n".join(
        [f"{i}. [CRITICAL] finding number {i} " + "x" * 400 for i in range(1, 9)]
        + ["9. an ordinary finding", "10. [CRITICAL] the last one"]
    )
    verdicts = "\n".join(f"{i}: ABSENT" for i in range(1, 11)) + "\nDROPPED: FAIL"
    out = absent_critical(findings, verdicts)
    assert len(out) == 6, "bounded"
    assert all(len(item) <= 300 for item in out)
    assert out[0].startswith("finding number 1") and "[CRITICAL]" not in out[0]
    assert absent_critical("1. [CRITICAL] a\n2. b", "1: PRESENT\n2: ABSENT\nDROPPED: PASS") == []


def test_the_orchestrator_appends_the_recovered_lines_to_the_summary_the_synthesis_reads(tmp_path: Path) -> None:
    from chimera.orchestration.hierarchy import _with_recovered

    text = _with_recovered("head of the summary", ("Full card numbers in the debug log for 9 days",))
    assert text.startswith("head of the summary")
    assert "## Recovered by the audit from the raw output" in text
    assert "- Full card numbers in the debug log for 9 days" in text


def test_end_to_end_the_synthesis_reads_the_recovered_lines(tmp_path: Path) -> None:
    """Through `run_prepared`: a verifier that recovers a finding changes what the top model reads."""
    from chimera.orchestration.envelope_verify import VerifyOutcome
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator
    from chimera.orchestration.spec import TaskSpec as Spec

    class _Gateway:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete(self, messages: Any, **kwargs: Any) -> CompletionResult:
            last = messages[-1]
            text = last["content"] if isinstance(last, dict) else last.content
            self.prompts.append(text)
            return CompletionResult(content="worker text", model="m", prompt_tokens=10, completion_tokens=5)

    class _Recovering:
        def verify(self, spec: Any, envelope: Any, *, force_spot: bool = False) -> VerifyOutcome:
            return VerifyOutcome(passed=True, stage="spot", checks_run=("schema", "spot", "recover"),
                                 recovered=("the finding the cut removed",))

    gateway = _Gateway()
    store = ArtifactStore(tmp_path)
    orchestrator = HierarchicalOrchestrator(
        gateway, weak_model="w", mid_model="m", top_model="t", store=store, verifier=_Recovering(),  # type: ignore[arg-type]
    )
    result = orchestrator.run_prepared("task", [Spec(task_id="s1", objective="do s1")])
    assert not result.fell_back
    assert "Recovered by the audit from the raw output" in result.envelopes[0].summary
    synthesis_prompt = gateway.prompts[-1]
    assert "the finding the cut removed" in synthesis_prompt
