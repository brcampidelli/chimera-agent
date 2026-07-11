"""Tests for the three-gate envelope verifier (M16-A5).

Includes the fixture-based catch-rate proof: seeded-bad envelopes MUST be caught
by gates 1/2 at zero model cost (the fake backend records whether it was called).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.spec import ResultEnvelope, TaskSpec
from chimera.providers.gateway import CompletionResult, MessageLike


class SpotBackend:
    """A scripted spot-checker; records every call so tests can prove zero-cost gates."""

    def __init__(self, verdict: str = "FAITHFUL — matches the raw output.") -> None:
        self.verdict = verdict
        self.calls = 0
        self.last_prompt: str = ""

    def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        last = messages[-1]
        self.last_prompt = last["content"] if isinstance(last, dict) else str(last)
        return CompletionResult(content=self.verdict, model="spot")


def _spec(**overrides: object) -> TaskSpec:
    base: dict[str, object] = {"task_id": "t1", "objective": "count the widgets"}
    base.update(overrides)
    return TaskSpec(**base)  # type: ignore[arg-type]


def _verifier(tmp_path: Path, backend: SpotBackend | None, *, spot_rate: float = 0.0,
              seed: int = 7) -> EnvelopeVerifier:
    return EnvelopeVerifier(
        store=ArtifactStore(tmp_path), backend=backend, spot_rate=spot_rate,
        rng=random.Random(seed),
    )


# ---------------------------------------------------------------------------
# Gate 1 — schema (free)
# ---------------------------------------------------------------------------


def test_schema_gate_catches_bad_envelopes_at_zero_model_cost(tmp_path: Path) -> None:
    backend = SpotBackend()
    verifier = _verifier(tmp_path, backend, spot_rate=1.0)  # spot WOULD run if reached
    seeded_bad = [
        ResultEnvelope(task_id="WRONG", summary="x"),                    # id mismatch
        ResultEnvelope(task_id="t1", status="ok", summary="  "),          # empty ok
        ResultEnvelope(task_id="t1", status="failed"),                    # failure, no why
    ]
    for env in seeded_bad:
        outcome = verifier.verify(_spec(), env)
        assert outcome.passed is False
        assert outcome.stage == "schema"
    assert backend.calls == 0  # every catch was free


# ---------------------------------------------------------------------------
# Gate 2 — acceptance criteria (deterministic)
# ---------------------------------------------------------------------------


def test_criteria_gate_uses_regex_clauses_from_output_format(tmp_path: Path) -> None:
    backend = SpotBackend()
    verifier = _verifier(tmp_path, backend)
    spec = _spec(output_format="A count.\nregex: \\d+ widgets")
    bad = ResultEnvelope(task_id="t1", summary="many widgets, trust me")
    outcome = verifier.verify(spec, bad)
    assert outcome.passed is False and outcome.stage == "criteria"
    assert "widgets" in outcome.detail
    good = ResultEnvelope(task_id="t1", summary="I found 42 widgets")
    assert verifier.verify(spec, good).passed is True
    assert backend.calls == 0  # criteria are model-free


# ---------------------------------------------------------------------------
# Gate 3 — spot check
# ---------------------------------------------------------------------------


def test_spot_check_forced_by_gaps_and_passes_when_faithful(tmp_path: Path) -> None:
    backend = SpotBackend("FAITHFUL — summary matches.")
    verifier = _verifier(tmp_path, backend, spot_rate=0.0)  # only gaps can trigger it
    store = ArtifactStore(tmp_path)
    raw = "widget row\n" * 3_000
    env = build_envelope(_spec(), raw, store, gaps=["could not check source B"])
    outcome = verifier.verify(_spec(), env)
    assert outcome.passed is True and outcome.stage == "spot"
    assert backend.calls == 1
    assert "Raw output" in backend.last_prompt  # artifact went to the VERIFIER's context


def test_spot_check_unfaithful_escalates(tmp_path: Path) -> None:
    backend = SpotBackend("UNFAITHFUL — the summary invents a count absent from the output.")
    verifier = _verifier(tmp_path, backend, spot_rate=0.0)
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "data\n" * 3_000, store, gaps=["unsure"])
    outcome = verifier.verify(_spec(), env)
    assert outcome.passed is False and outcome.stage == "spot"
    assert outcome.escalate is True


def test_spot_check_probabilistic_with_seeded_rng(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    raw = "row\n" * 5_000
    env = build_envelope(_spec(), raw, store)  # no gaps -> only the rng can trigger
    always = _verifier(tmp_path, SpotBackend(), spot_rate=1.0)
    assert always.verify(_spec(), env).stage == "spot"
    never = _verifier(tmp_path, SpotBackend(), spot_rate=0.0)
    assert never.verify(_spec(), env).stage == "accepted"


def test_force_spot_audits_even_without_gaps_or_rng(tmp_path: Path) -> None:
    # A re-ask triggered by a spot failure must be spot-checked again — not re-accepted on the free
    # schema+criteria gates ~80% of the time (spot_rate=0.0 here would otherwise skip it).
    backend = SpotBackend("UNFAITHFUL — the retry still invents a figure.")
    verifier = _verifier(tmp_path, backend, spot_rate=0.0)
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "row\n" * 5_000, store)  # no gaps -> normally skipped
    assert verifier.verify(_spec(), env).stage == "accepted"
    forced = verifier.verify(_spec(), env, force_spot=True)
    assert forced.stage == "spot" and forced.passed is False


def test_missing_evidence_ref_fails_with_escalation(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path, SpotBackend(), spot_rate=1.0)
    env = ResultEnvelope(task_id="t1", summary="fine", evidence_refs=["ghost.txt"],
                         gaps=["x"])
    outcome = verifier.verify(_spec(), env)
    assert outcome.passed is False and outcome.stage == "spot"
    assert outcome.escalate is True


def test_spot_backend_error_passes_through_never_crashes(tmp_path: Path) -> None:
    class ExplodingBackend:
        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            raise RuntimeError("provider down")

    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "data\n" * 3_000, store, gaps=["x"])
    verifier = EnvelopeVerifier(store=store, backend=ExplodingBackend(), spot_rate=1.0)
    outcome = verifier.verify(_spec(), env)
    assert outcome.passed is True and outcome.stage == "accepted"  # degraded, not dead


def test_no_backend_means_no_spot_stage(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "data\n" * 3_000, store, gaps=["x"])
    verifier = EnvelopeVerifier(store=store, backend=None, spot_rate=1.0)
    assert verifier.verify(_spec(), env).stage == "accepted"


# ---------------------------------------------------------------------------
# M18-2 — decomposed criteria + cross-provider auditing
# ---------------------------------------------------------------------------


def test_grade_faithfulness_decomposed_and_legacy() -> None:
    from chimera.orchestration.envelope_verify import _grade_faithfulness

    assert _grade_faithfulness("INVENTED: PASS\nDROPPED: PASS\nCONTRADICTION: PASS\nlooks fine") is True
    assert _grade_faithfulness("INVENTED: FAIL\nDROPPED: PASS\nCONTRADICTION: PASS\ninvents a count") is False
    assert _grade_faithfulness("DROPPED: FAIL — omits the error total") is False
    assert _grade_faithfulness("CONTRADICTION: FAIL") is False
    assert _grade_faithfulness("FAITHFUL — matches") is True  # legacy holistic verdict
    assert _grade_faithfulness("UNFAITHFUL — invents") is False  # legacy holistic verdict
    assert _grade_faithfulness("(the model rambled with no verdict)") is True  # garbled -> non-blocking


def test_spot_check_decomposed_fail_escalates(tmp_path: Path) -> None:
    backend = SpotBackend("INVENTED: FAIL\nDROPPED: PASS\nCONTRADICTION: PASS\nsummary invents a total")
    verifier = _verifier(tmp_path, backend, spot_rate=0.0)
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "rows\n" * 3_000, store, gaps=["unsure"])
    outcome = verifier.verify(_spec(), env)
    assert outcome.passed is False and outcome.stage == "spot" and outcome.escalate is True


def test_cross_provider_auditor_is_used_over_worker_backend(tmp_path: Path) -> None:
    worker = SpotBackend("INVENTED: PASS\nDROPPED: PASS\nCONTRADICTION: PASS")
    auditor = SpotBackend("INVENTED: FAIL\nDROPPED: PASS\nCONTRADICTION: PASS\ndistinct provider caught it")
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "data\n" * 3_000, store, gaps=["x"])
    verifier = EnvelopeVerifier(
        store=store, backend=worker, verifier_backend=auditor, model="worker-m",
        verifier_model="auditor-m", spot_rate=1.0, rng=random.Random(7),
    )
    outcome = verifier.verify(_spec(), env)
    assert auditor.calls == 1 and worker.calls == 0  # the independent auditor graded, not the worker
    assert outcome.passed is False and outcome.escalate is True
