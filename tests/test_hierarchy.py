"""Offline tests for the HierarchicalOrchestrator (M16-A7). No model calls — FakeBackend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.orchestration.artifacts import ArtifactStore
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.hierarchy import (
    WORKER_SYSTEM,
    HierarchicalOrchestrator,
    HierarchyConfig,
    classify_task,
)
from chimera.orchestration.receipts import load_delegations
from chimera.providers.gateway import CompletionResult, Message, MessageLike

WEAK, MID, TOP = "w/model:free", "m/model", "t/model"

_DECOMPOSITION = json.dumps(
    [
        {"objective": "Summarize doc A", "output_format": "3 bullets", "boundaries": "doc A only"},
        {"objective": "Summarize doc B", "output_format": "3 bullets", "boundaries": "doc B only"},
    ]
)

_READ_TASK = (
    "Research and compare the release notes in doc A and doc B, extract the breaking "
    "changes from each, and summarize what upgrading requires; list the risks as well."
)


class FakeBackend:
    """Scripted by model slug + call order; records every call for assertions."""

    def __init__(self, decomposition: str = _DECOMPOSITION) -> None:
        self.decomposition = decomposition
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        system = ""
        first = messages[0]
        data = first.as_dict() if isinstance(first, Message) else first
        if data.get("role") == "system":
            system = str(data.get("content", ""))
        self.calls.append({"model": model, "system": system})
        if "Split the user's task" in system:
            content = self.decomposition
        elif system == WORKER_SYSTEM:
            content = "Findings: everything nominal.\n\nGaps\n(none)"
        elif "Synthesize ONE final answer" in system:
            content = "Final synthesized answer."
        else:
            content = "single-agent answer"
        return CompletionResult(
            content=content, model=model or "?", prompt_tokens=100, completion_tokens=50
        )


def _orchestrator(
    backend: FakeBackend, tmp_path: Path, **config: Any
) -> HierarchicalOrchestrator:
    store = ArtifactStore(tmp_path / "artifacts")
    return HierarchicalOrchestrator(
        backend,
        weak_model=WEAK,
        mid_model=MID,
        top_model=TOP,
        store=store,
        verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
        receipts_path=tmp_path / "delegations.jsonl",
        config=HierarchyConfig(**config) if config else None,
    )


# ---------------------------------------------------------------------------
# Classifier (deterministic, table-driven)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        (_READ_TASK, "parallel_read"),
        ("Fix the bug in auth.py and add a regression test", "sequential_write"),
        ("Implement the new login flow", "sequential_write"),
        ("Corrija o erro no arquivo main.py", "sequential_write"),
        ("What is the capital of France?", "simple"),
        ("hi", "simple"),
        ("Research the history of Rome", "simple"),  # read intent but single-part & short
        # 2+ distinct sources + read intent -> guaranteed-gain region, even terse:
        ("Compare a.md and b.md", "parallel_read"),
        ("Summarize doc A and doc B", "parallel_read"),
        ("Read report.pdf", "simple"),  # ONE source -> nothing to isolate
    ],
)
def test_classifier_table(task: str, expected: str) -> None:
    assert classify_task(task) == expected


def test_count_sources_counts_distinct_refs() -> None:
    from chimera.orchestration.hierarchy import count_sources

    assert count_sources("compare a.md and b.md") == 2
    assert count_sources("read a.md, a.md again, and b.md") == 2  # distinct only
    assert count_sources("summarize doc A and document B") == 2
    assert count_sources("check https://x.com/1 and https://y.com/2") == 2
    assert count_sources("what is 2+2") == 0


def test_two_sources_skip_the_profitability_veto(tmp_path: Path) -> None:
    """A terse 2-source read task must delegate (guaranteed-gain region), not be
    vetoed by the crude blank-context profitability estimate."""
    backend = FakeBackend()
    result = _orchestrator(backend, tmp_path).run("Compare alpha.md and bravo.md")
    assert result.shape == "parallel_read"
    assert result.fell_back is False


# ---------------------------------------------------------------------------
# End-to-end (scripted)
# ---------------------------------------------------------------------------


def test_parallel_read_flows_decompose_dispatch_synthesize(tmp_path: Path) -> None:
    backend = FakeBackend()
    orchestrator = _orchestrator(backend, tmp_path)
    result = orchestrator.run(_READ_TASK)
    assert result.fell_back is False
    assert result.shape == "parallel_read"
    assert result.answer == "Final synthesized answer."
    assert len(result.envelopes) == 2
    assert len(result.receipts) == 2
    # Receipts persisted with counterfactuals in the same rows.
    persisted = load_delegations(tmp_path / "delegations.jsonl")
    assert len(persisted) == 2
    assert all(r.counterfactual_tokens for r in persisted)
    assert result.total_tokens and result.total_tokens > 0


def test_workers_share_byte_identical_system_prefix(tmp_path: Path) -> None:
    backend = FakeBackend()
    _orchestrator(backend, tmp_path).run(_READ_TASK)
    worker_prompts = [c["system"] for c in backend.calls if c["system"] == WORKER_SYSTEM]
    assert len(worker_prompts) == 2
    assert len({p for p in worker_prompts}) == 1  # byte-identical -> shared cache prefix


def test_workers_use_mid_model_and_never_rotate(tmp_path: Path) -> None:
    backend = FakeBackend()
    _orchestrator(backend, tmp_path).run(_READ_TASK)
    worker_models = {c["model"] for c in backend.calls if c["system"] == WORKER_SYSTEM}
    assert worker_models == {MID}


def test_write_task_falls_back_and_is_audited(tmp_path: Path) -> None:
    backend = FakeBackend()
    orchestrator = _orchestrator(backend, tmp_path)
    result = orchestrator.run("Fix the bug in auth.py and add a regression test")
    assert result.fell_back is True
    assert result.shape == "sequential_write"
    assert result.answer == "single-agent answer"
    # The fallback decision itself lands in the receipts (auditable).
    persisted = load_delegations(tmp_path / "delegations.jsonl")
    assert len(persisted) == 1
    assert persisted[0].tier == "top"
    # And no worker was ever spawned.
    assert not any(c["system"] == WORKER_SYSTEM for c in backend.calls)


def test_simple_task_falls_back(tmp_path: Path) -> None:
    backend = FakeBackend()
    result = _orchestrator(backend, tmp_path).run("What is the capital of France?")
    assert result.fell_back is True


def test_bad_decomposition_repairs_once_then_falls_back(tmp_path: Path) -> None:
    class BadJSONBackend(FakeBackend):
        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            result = super().complete(messages, **kwargs)
            if "Split the user's task" in self.calls[-1]["system"]:
                return CompletionResult(content="not json, sorry", model="t")
            return result

    backend = BadJSONBackend()
    result = _orchestrator(backend, tmp_path).run(_READ_TASK)
    assert result.fell_back is True  # two failed parses -> single-agent path
    decompose_calls = [c for c in backend.calls if "Split the user's task" in c["system"]]
    assert len(decompose_calls) == 2  # exactly ONE repair retry


def test_unverified_envelope_is_dropped_not_synthesized(tmp_path: Path) -> None:
    """A result the verifier rejects (even after the re-ask) must NOT reach synthesis —
    it is dropped, but still audited via a receipt. Guards the M16-A5 contract."""
    from chimera.orchestration.envelope_verify import VerifyOutcome

    class FailingVerifier:
        def verify(self, spec: Any, envelope: Any) -> VerifyOutcome:
            return VerifyOutcome(passed=False, stage="criteria", detail="does not match")

    backend = FakeBackend()
    store = ArtifactStore(tmp_path / "artifacts")
    orchestrator = HierarchicalOrchestrator(
        backend, weak_model=WEAK, mid_model=MID, top_model=TOP,
        store=store, verifier=FailingVerifier(),  # type: ignore[arg-type]
        receipts_path=tmp_path / "delegations.jsonl",
    )
    result = orchestrator.run(_READ_TASK)
    # No unverified envelope reaches synthesis; with ALL workers rejected the
    # orchestrator recovers via the single-agent fallback rather than shipping nothing.
    assert result.envelopes == []
    assert result.fell_back is True
    assert result.answer == "single-agent answer"
    # The rejected worker attempts AND the fallback are all audited.
    persisted = load_delegations(tmp_path / "delegations.jsonl")
    assert [r.tier for r in persisted] == ["mid", "mid", "top"]


def test_trivial_subtask_runs_inline_skipping_worker(tmp_path: Path) -> None:
    """With the per-subtask gate on, a tiny spec is answered inline by the TOP model
    in one call — no mid-worker spawn, no verification round-trip."""
    backend = FakeBackend()
    orchestrator = _orchestrator(backend, tmp_path, inline_below_spec_tokens=100_000)
    result = orchestrator.run(_READ_TASK)
    assert result.fell_back is False
    assert len(result.envelopes) == 2
    # No subtask went to a MID worker; both were handled inline by the TOP model.
    mid_worker_calls = [c for c in backend.calls if c["system"] == WORKER_SYSTEM and c["model"] == MID]
    top_inline_calls = [c for c in backend.calls if c["system"] == WORKER_SYSTEM and c["model"] == TOP]
    assert mid_worker_calls == []
    assert len(top_inline_calls) == 2
    # Audited as top-tier delegations with the (delegate) counterfactual recorded.
    persisted = load_delegations(tmp_path / "delegations.jsonl")
    assert [r.tier for r in persisted] == ["top", "top"]
    assert all(r.counterfactual_tokens for r in persisted)


def test_conflicting_requires_both_overlap_and_a_marker(tmp_path: Path) -> None:
    """_conflicting must not fire on a lone 'however' with no shared topic (false
    positive -> wasted fusion), and must fire on same-topic disagreement."""
    from chimera.orchestration.hierarchy import _conflicting
    from chimera.orchestration.spec import ResultEnvelope

    unrelated = [
        ResultEnvelope(task_id="a", status="ok", summary="Revenue however grew in Europe markets"),
        ResultEnvelope(task_id="b", status="ok", summary="Penguins waddle across Antarctic tundra"),
    ]
    assert _conflicting(unrelated) is False

    same_topic = [
        ResultEnvelope(task_id="c", status="ok", summary="The revenue figure grew twelve percent overall"),
        ResultEnvelope(task_id="d", status="ok", summary="However the revenue figure fell twelve percent instead"),
    ]
    assert _conflicting(same_topic) is True


def test_worker_count_capped_by_effort_policy(tmp_path: Path) -> None:
    many = json.dumps(
        [{"objective": f"part {i}", "output_format": "", "boundaries": ""} for i in range(9)]
    )
    backend = FakeBackend(decomposition=many)
    result = _orchestrator(backend, tmp_path).run(_READ_TASK)
    assert len(result.envelopes) == 4  # EffortPolicy.max_parallel_workers default


def test_synthesis_sees_summaries_never_artifacts(tmp_path: Path) -> None:
    class BulkyWorkerBackend(FakeBackend):
        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            result = super().complete(messages, **kwargs)
            if self.calls[-1]["system"] == WORKER_SYSTEM:
                return CompletionResult(
                    content="FINDING-HEAD\n" + ("bulk line\n" * 4000) + "FINDING-TAIL",
                    model=MID, prompt_tokens=100, completion_tokens=50,
                )
            return result

    backend = BulkyWorkerBackend()
    orchestrator = _orchestrator(backend, tmp_path)
    result = orchestrator.run(_READ_TASK)
    assert result.fell_back is False
    synth = [c for c in backend.calls if "Synthesize ONE final answer" in c["system"]]
    assert synth, "synthesis stage must run"
    # Every envelope was compacted: the synthesis prompt cannot exceed the caps.
    for env in result.envelopes:
        assert len(env.summary) <= 8_000
        assert env.evidence_refs  # bulk went to the artifact store


def test_dry_run_spends_no_worker_tokens(tmp_path: Path) -> None:
    backend = FakeBackend()
    plan = _orchestrator(backend, tmp_path).dry_run(_READ_TASK)
    assert plan["shape"] == "parallel_read"
    assert plan["subtasks"] == ["Summarize doc A", "Summarize doc B"]
    assert plan["workers"] == 2
    assert not any(c["system"] == WORKER_SYSTEM for c in backend.calls)  # zero worker spend
