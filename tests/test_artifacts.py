"""Tests for the artifact store + envelope compaction (M16-A2).

Includes the honest offline compaction measurement: over canned long worker
transcripts, how many chars actually reach the parent with vs without the
envelope. No model calls; the number is measured, not claimed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.orchestration.artifacts import ArtifactStore, build_envelope
from chimera.orchestration.spec import SUMMARY_MAX_CHARS, TaskSpec, validate_envelope


def _spec(task_id: str = "t1") -> TaskSpec:
    return TaskSpec(task_id=task_id, objective="collect the findings")


def test_put_get_round_trip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.put("hello world", task_id="t1")
    assert store.get(ref) == "hello world"
    assert store.get(ref.path) == "hello world"
    assert ref.chars == len("hello world")


def test_put_dedups_identical_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    a = store.put("same content", task_id="t1")
    b = store.put("same content", task_id="t1")
    assert a.path == b.path
    assert len(list(tmp_path.iterdir())) == 1


def test_for_run_namespaces_and_sanitizes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path).for_run("run/..\\weird id")
    ref = store.put("x")
    assert (store.root / ref.path).exists()
    assert store.root.parent == tmp_path  # stayed inside the root
    assert ".." not in store.root.name and "/" not in store.root.name


def test_refs_lists_stored_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    assert store.refs() == []  # missing dir is fine
    store.put("aaa", task_id="t1")
    store.put("bbb", task_id="t2")
    listed = store.refs()
    assert len(listed) == 2
    assert all(r.sha256 for r in listed)


def test_small_output_is_the_summary_no_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "short result", store)
    assert env.summary == "short result"
    assert env.evidence_refs == []
    assert not tmp_path.exists() or not any(tmp_path.iterdir())  # nothing written


def test_large_output_spills_to_artifact_with_marker(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    raw = "finding line\n" * 5_000  # ~65k chars
    env = build_envelope(_spec(), raw, store)
    assert len(env.summary) <= SUMMARY_MAX_CHARS
    assert "[truncated — full output in evidence]" in env.summary
    assert len(env.evidence_refs) == 1
    assert store.get(env.evidence_refs[0]) == raw  # full fidelity in the store
    # And the envelope passes the free schema gate.
    assert validate_envelope(_spec(), env) == []


def test_envelope_preserves_status_and_gaps(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "partial data", store, status="partial", gaps=["source B down"])
    assert env.status == "partial"
    assert env.gaps == ["source B down"]
    weird = build_envelope(_spec(), "x", store, status="exploded")
    assert weird.status == "ok"  # unknown status normalizes, never crashes


def test_compaction_ratio_measured_on_canned_transcripts(tmp_path: Path) -> None:
    """The honest offline number: chars delivered to the parent, with vs without.

    Canned transcripts mimic real worker output shapes (tool logs + findings).
    We assert the envelope path delivers <= 15% of the raw chars on bulky
    transcripts and record the measured ratio in the assertion message.
    """
    store = ArtifactStore(tmp_path)
    transcripts = [
        ("web-research", "GET https://example.org/page ->\n" + ("result row | data | 2026\n" * 3_000)),
        ("file-scan", "\n".join(f"src/module_{i}.py: 120 lines, 3 defs" for i in range(4_000))),
        ("log-analysis", ("2026-07-07T12:00:00 INFO worker step ok\n" * 2_500) + "SUMMARY: 3 errors found"),
    ]
    total_raw = 0
    total_delivered = 0
    for name, raw in transcripts:
        env = build_envelope(_spec(name), raw, store)
        total_raw += len(raw)
        total_delivered += len(env.summary)
        assert store.get(env.evidence_refs[0]) == raw  # nothing lost, only relocated
    ratio = total_delivered / total_raw
    assert ratio <= 0.15, f"compaction too weak: parent receives {ratio:.1%} of raw chars"


@pytest.mark.parametrize("size", [SUMMARY_MAX_CHARS, SUMMARY_MAX_CHARS + 1])
def test_cap_boundary_is_exact(tmp_path: Path, size: int) -> None:
    store = ArtifactStore(tmp_path)
    env = build_envelope(_spec(), "y" * size, store)
    if size <= SUMMARY_MAX_CHARS:
        assert env.evidence_refs == []
    else:
        assert env.evidence_refs, "one char over the cap must spill"
