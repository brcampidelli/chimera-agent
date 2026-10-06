"""`build_receipt` copies every field an attempt carries — and a sensible default for each it lacks.

`chimera/api/runs.py` was in the mutation gate from the start, but the receipt grew a field at a time
(model, provider, cost, tokens, failure class, prompt digest...) in commits whose tests lived in new
files the gate never ran, and 106 mutants survived. Most were the same shape: the field copied from
the wrong attribute, the default for a missing one changed, the bound on a list moved by one. A
receipt is what the Runs screen, the weekly review and the cost comparisons read; a field silently
blank there is a wrong number nobody can see is wrong.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chimera.api.runs import (
    AttemptReceipt,
    append_run,
    build_receipt,
    cost_per_accepted_change,
    load_runs,
    total_usd,
)


def _attempt(**fields: Any) -> SimpleNamespace:
    base = dict(index=0, verified=True, reverted=False, success=True, verify_output="ok",
                diff_summary="", feedback="")
    base.update(fields)
    return SimpleNamespace(**base)


def _result(*attempts: SimpleNamespace, **fields: Any) -> Any:
    base = dict(success=True, paused=False, answer="", attempts=list(attempts))
    base.update(fields)
    return SimpleNamespace(**base)


FULL = dict(
    evidence="test", run_id="r-1", diff_flags=[f"f{i}" for i in range(60)],
    tool_names=[f"t{i}" for i in range(210)], usd=0.25, overhead_usd=0.05, prompt_tokens=11,
    completion_tokens=7, model="m-1", provider="p-1", discarded_at="2026-10-05",
    verified_fingerprint="fp", failure_class="timeout", failure_evidence="e" * 600,
    system_sha="abc123",
)


def test_every_attempt_field_is_copied() -> None:
    receipt = build_receipt(_result(_attempt(**FULL), stagnant=True, stopped_reason="cap",
                                    ending="verified"), "task", "pytest", "ts", profile="p")
    a = receipt.attempts[0]
    assert (a.evidence, a.run_id, a.usd, a.overhead_usd) == ("test", "r-1", 0.25, 0.05)
    assert a.diff_flags == [f"f{i}" for i in range(50)]
    assert a.tool_names == [f"t{i}" for i in range(200)]
    assert (a.prompt_tokens, a.completion_tokens, a.model, a.provider) == (11, 7, "m-1", "p-1")
    assert (a.discarded_at, a.verified_fingerprint, a.failure_class) == ("2026-10-05", "fp", "timeout")
    assert a.failure_evidence == "e" * 500
    assert a.system_sha == "abc123"
    assert (receipt.stagnant, receipt.profile, receipt.usd) == (True, "p", 0.25)
    assert (receipt.stopped_reason, receipt.ending) == ("cap", "verified")


def test_an_attempt_without_the_newer_fields_gets_their_empty_defaults() -> None:
    receipt = build_receipt(_result(_attempt()), "task", None, "ts")
    a = receipt.attempts[0]
    assert (a.evidence, a.run_id, a.diff_flags, a.tool_names) == ("none", "", [], [])
    assert (a.usd, a.overhead_usd, a.prompt_tokens, a.completion_tokens) == (None, None, 0, 0)
    assert (a.model, a.provider, a.discarded_at, a.verified_fingerprint) == ("", "", "", "")
    assert (a.failure_class, a.failure_evidence, a.system_sha) == ("", "", "")
    assert (receipt.verify_source, receipt.profile_source, receipt.workspace) == ("user", "user", "")
    assert (receipt.stopped_reason, receipt.ending, receipt.stagnant) == ("", "unknown", None)
    assert (receipt.profile, receipt.usd) == (None, None)


def test_none_and_empty_values_on_an_attempt_read_as_the_defaults() -> None:
    blank = dict(evidence="", run_id=None, model=None, provider=None, discarded_at=None,
                 verified_fingerprint=None, failure_class=None, failure_evidence=None,
                 system_sha=None, prompt_tokens=None, completion_tokens=None)
    receipt = build_receipt(_result(_attempt(**blank), stopped_reason=None, ending=None), "t", None, "ts")
    a = receipt.attempts[0]
    assert (a.evidence, a.run_id, a.model, a.provider) == ("none", "", "", "")
    assert (a.discarded_at, a.verified_fingerprint, a.failure_class) == ("", "", "")
    assert (a.failure_evidence, a.system_sha, a.prompt_tokens, a.completion_tokens) == ("", "", 0, 0)
    assert (receipt.stopped_reason, receipt.ending) == ("", "unknown")


def test_a_zero_cost_leg_adds_zero_and_the_sum_is_rounded_to_six_places() -> None:
    def leg(usd: float | None) -> AttemptReceipt:
        return AttemptReceipt(index=0, verified=True, reverted=False, success=True, verify_output="",
                              diff_summary="", feedback="", usd=usd)

    assert total_usd([leg(0.0), leg(0.5)]) == 0.5
    assert total_usd([leg(0.1234567)]) == 0.123457
    cost = cost_per_accepted_change([
        AttemptReceipt(index=0, verified=True, reverted=False, success=True, verify_output="",
                       diff_summary="", feedback="", usd=1.0, diff_productive=True),
        AttemptReceipt(index=1, verified=True, reverted=False, success=True, verify_output="",
                       diff_summary="", feedback="", usd=0.0, diff_productive=True),
        AttemptReceipt(index=2, verified=True, reverted=False, success=True, verify_output="",
                       diff_summary="", feedback="", usd=0.0, diff_productive=True),
    ])
    assert cost.per_change == 0.333333


def test_a_receipt_line_with_bytes_that_are_not_utf8_does_not_stop_the_load(tmp_path: Path) -> None:
    # A log written by another tool, or cut mid-character by a crash, holds bytes that are not UTF-8.
    # They are replaced; the receipts around them, and the damaged one itself, still load.
    path = tmp_path / "runs.jsonl"
    append_run(path, build_receipt(_result(_attempt()), "first", None, "ts1"))
    append_run(path, build_receipt(_result(_attempt()), "café", None, "ts2"))
    path.write_bytes(path.read_bytes().replace("café".encode(), b"caf\xe9"))
    append_run(path, build_receipt(_result(_attempt()), "third", None, "ts3"))
    assert [r.task for r in load_runs(path)] == ["first", "caf�", "third"]
