"""The orchestration event seam: what a live consumer is told, and what it is never told.

These tests exist because the frames are a contract with a UI that cannot read the orchestrator's
internals. Three properties matter more than any individual assertion:

- a run with no sink behaves EXACTLY as before (the seam is invisible when unused);
- a sink that raises does not kill the run (progress reporting is advisory);
- the two ways a worker can fail to produce a usable answer stay distinguishable.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from chimera.orchestration.events import OrchEvent, thread_safe_sink
from chimera.orchestration.hierarchy import HierarchicalOrchestrator
from tests.test_hierarchy import _READ_TASK, FakeBackend, _orchestrator


class Recorder:
    """A sink that is itself thread-safe, so a lost frame here is the orchestrator's fault."""

    def __init__(self) -> None:
        self.events: list[OrchEvent] = []
        self._lock = threading.Lock()

    def __call__(self, event: OrchEvent) -> None:
        with self._lock:
            self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def of(self, kind: str) -> list[OrchEvent]:
        return [e for e in self.events if e.kind == kind]


def _watched(tmp_path: Path, sink: Any, **config: Any) -> HierarchicalOrchestrator:
    orch = _orchestrator(FakeBackend(), tmp_path, **config)
    orch.on_event = sink
    return orch


def test_a_fan_out_reports_every_stage_in_order(tmp_path: Path) -> None:
    rec = Recorder()
    _watched(tmp_path, rec).run(_READ_TASK)

    kinds = rec.kinds()
    assert kinds[0] == "classified", "the shape decision comes before anything is spent"
    assert kinds.index("decomposed") < kinds.index("worker_started")
    assert kinds.index("synthesizing") < kinds.index("done")
    assert kinds[-1] == "done"
    assert "fell_back" not in kinds

    classified = rec.of("classified")[0]
    assert classified.data["shape"] == "parallel_read"
    # The source count travels with the shape: it is what decides whether the profitability guard
    # applies at all, so a consumer showing the shape without it shows half the reason.
    assert classified.data["sources"] >= 2


def test_every_worker_frame_carries_its_own_task_id(tmp_path: Path) -> None:
    rec = Recorder()
    _watched(tmp_path, rec).run(_READ_TASK)

    started = rec.of("worker_started")
    assert len(started) == 2
    ids = {e.task_id for e in started}
    assert len(ids) == 2 and all(ids), "a frame without a route lands in the wrong card"
    assert {e.task_id for e in rec.of("worker_verified")} == ids
    assert all(e.data["tier"] == "mid" for e in started)


def test_the_decomposition_is_published_before_the_workers_start(tmp_path: Path) -> None:
    rec = Recorder()
    _watched(tmp_path, rec).run(_READ_TASK)

    published = {spec["task_id"] for spec in rec.of("decomposed")[0].data["specs"]}
    worked = {e.task_id for e in rec.of("worker_started")}
    # The ids must match, or a consumer cannot pre-create the cards and workers appear from
    # nowhere as each one starts.
    assert published == worked


def test_a_write_task_says_it_fell_back_and_never_pretends_to_have_workers(tmp_path: Path) -> None:
    rec = Recorder()
    _watched(tmp_path, rec).run("Refactor the parser and fix the failing test")

    assert rec.kinds() == ["classified", "fell_back", "done"]
    fell = rec.of("fell_back")[0]
    # A code, not the prose: the reason string is a log line and has been reworded before. A UI
    # matching on its text is a UI that breaks when someone improves a debug message.
    assert fell.data["reason"] == "shape"
    assert fell.data["shape"] == "sequential_write"
    assert rec.of("done")[0].data["fell_back"] is True


def test_a_rejected_worker_says_which_stage_refused_it(tmp_path: Path) -> None:
    class FailingVerifier:
        def verify(self, spec: Any, envelope: Any, *, force_spot: bool = False) -> Any:
            class Outcome:
                passed = False
                stage = "criteria"
                detail = "the summary does not answer the objective"

            return Outcome()

    rec = Recorder()
    orch = _watched(tmp_path, rec)
    orch.verifier = FailingVerifier()  # type: ignore[assignment]
    orch.run(_READ_TASK)

    rejected = rec.of("worker_rejected")
    assert rejected, "a dropped envelope nobody was told about is a silent gap in the answer"
    assert all(e.data["reason"] == "verifier" for e in rejected)
    assert all(e.data["stage"] == "criteria" for e in rejected)
    assert all("does not answer" in e.data["detail"] for e in rejected)


def test_a_worker_with_no_output_is_not_reported_as_a_verifier_rejection(tmp_path: Path) -> None:
    class SilentBackend(FakeBackend):
        def complete(self, messages: Any, **kwargs: Any) -> Any:
            result = super().complete(messages, **kwargs)
            if self.calls[-1]["system"].startswith("You are a focused sub-worker"):
                result.content = ""
            return result

    rec = Recorder()
    orch = _orchestrator(SilentBackend(), tmp_path)
    orch.on_event = rec
    orch.run(_READ_TASK)

    rejected = rec.of("worker_rejected")
    assert rejected
    # The distinction the display depends on: a provider fault is not a judgement about a model.
    assert all(e.data["reason"] == "no_output" for e in rejected)


def test_a_sink_that_raises_does_not_take_the_run_down(tmp_path: Path) -> None:
    def explode(event: OrchEvent) -> None:
        raise RuntimeError("the consumer went away")

    result = _watched(tmp_path, explode).run(_READ_TASK)

    # Tokens already spent must still buy an answer. A closed socket is not a reason to lose one.
    assert result.answer == "Final synthesized answer."
    assert result.fell_back is False


def test_without_a_sink_the_run_is_unchanged(tmp_path: Path) -> None:
    backend = FakeBackend()
    plain = _orchestrator(backend, tmp_path).run(_READ_TASK)
    calls_without = len(backend.calls)

    backend2 = FakeBackend()
    watched = _orchestrator(backend2, tmp_path)
    watched.on_event = Recorder()
    result = watched.run(_READ_TASK)

    # Same answer, same number of model calls, same receipts. Observing a run must not change it.
    assert result.answer == plain.answer
    assert len(backend2.calls) == calls_without
    assert len(result.receipts) == len(plain.receipts)


def test_stopping_skips_the_synthesis_it_was_asked_not_to_pay_for(tmp_path: Path) -> None:
    backend = FakeBackend()
    orch = _orchestrator(backend, tmp_path)
    orch.should_stop = lambda: True

    result = orch.run(_READ_TASK)

    assert result.cancelled is True
    assert result.answer == ""
    systems = [call["system"] for call in backend.calls]
    # Stopping before the workers start means no worker call was made, and no synthesis either.
    assert not any(s.startswith("You are a focused sub-worker") for s in systems)
    assert not any("Synthesize ONE final answer" in s for s in systems)


def test_concurrent_workers_do_not_lose_frames(tmp_path: Path) -> None:
    seen: list[OrchEvent] = []
    sink = thread_safe_sink(seen.append)

    orch = _watched(tmp_path, sink, max_workers=4)
    orch.run(_READ_TASK)

    # Two workers, two starts, and nothing dropped by the fan-in.
    assert len([e for e in seen if e.kind == "worker_started"]) == 2
    assert len([e for e in seen if e.kind == "done"]) == 1
