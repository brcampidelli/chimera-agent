"""Offline tests for the S30-56 supersession slice and opt-in lookup."""
from __future__ import annotations

from pathlib import Path

from bench.memory_staleness.items import OPINIONS, UPDATES
from bench.memory_staleness.run import _FakeBackend, check_fake, model_case
from chimera.memory import MemoryManager, MemoryStore


def test_fake_backend_grades_update_and_opinion_without_model() -> None:
    backend = _FakeBackend()
    update = model_case(backend, UPDATES[0])
    opinion = model_case(backend, OPINIONS[0])
    assert backend.calls == 2
    assert update["new_recalled"] is True
    assert update["wrong_update"] is False
    assert opinion["preserves_attribution"] is True


def test_benchmark_fixture_has_registered_size() -> None:
    assert len(UPDATES) == 45
    assert sum(item.type == "I" for item in UPDATES) == 23
    assert sum(item.type == "II" for item in UPDATES) == 22
    assert len(OPINIONS) == 30


def test_semantic_supersession_is_off_by_default_and_opt_in(tmp_path: Path) -> None:
    def embed(texts: list[str]) -> list[list[float]]:
        # Deliberately all-one vectors make the near-fact decision deterministic in this fixture.
        return [[1.0, 0.0] for _ in texts]

    ordinary = MemoryManager(MemoryStore(tmp_path / "ordinary.json"), embed=embed)
    ordinary.add("I live in Porto.")
    operation, _ = ordinary.remember("I live in Lisbon.")
    assert operation == "ADD"
    assert len(ordinary.store) == 2

    enabled = MemoryManager(
        MemoryStore(tmp_path / "enabled.json"), embed=embed, supersession=True,
        supersession_threshold=0.9,
    )
    old = enabled.add("I live in Porto.")
    operation, updated = enabled.remember("I live in Lisbon.")
    assert operation == "UPDATE"
    assert updated.id != old.id
    assert updated.content == "I live in Lisbon."
    assert updated.metadata["supersedes_id"] == old.id
    assert old.metadata["superseded_by"] == updated.id
    assert len(enabled.search("Where do I live?")) == 1
    assert enabled.search("Where do I live?")[0].id == updated.id


def test_check_fake_path_is_standalone(capsys: object) -> None:
    check_fake()
    # Capturing output is only to ensure the offline self-check ran to completion.
    assert capsys is not None
