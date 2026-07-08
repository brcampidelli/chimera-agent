"""Tests for the morning-brief recipe (M16-B3). Offline — no model calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.orchestration.brief import (
    BriefRecipe,
    brief_task,
    load_brief,
    specs_from_brief,
)

_RECIPE = """\
name: test-brief
topics:
  - "topic alpha"
  - "topic beta"
output_format: "2 bullets with sources"
synthesis: "one headline per topic"
"""


def test_load_brief_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "brief.yaml"
    path.write_text(_RECIPE, encoding="utf-8")
    recipe = load_brief(path)
    assert recipe.name == "test-brief"
    assert recipe.topics == ["topic alpha", "topic beta"]
    assert recipe.output_format == "2 bullets with sources"


def test_load_brief_rejects_empty_topics(tmp_path: Path) -> None:
    path = tmp_path / "brief.yaml"
    path.write_text("name: x\ntopics: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no topics"):
        load_brief(path)


def test_load_brief_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "brief.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_brief(path)


def test_specs_from_brief_is_deterministic_no_model() -> None:
    recipe = BriefRecipe(topics=["a", "b", "c"])
    specs = specs_from_brief(recipe, max_tokens=5_000)
    assert [s.task_id for s in specs] == ["topic-1", "topic-2", "topic-3"]
    assert all(s.effort.max_tokens == 5_000 for s in specs)
    assert all("Research the topic" in s.objective for s in specs)
    assert all(s.boundaries for s in specs)  # research-only boundary present
    assert brief_task(recipe).startswith("Produce ")


def test_shipped_example_recipe_is_valid() -> None:
    path = Path(__file__).resolve().parents[1] / "examples" / "morning_brief" / "brief.yaml"
    recipe = load_brief(path)
    assert len(recipe.topics) >= 2


def test_run_prepared_uses_recipe_split_without_decompose(tmp_path: Path) -> None:
    """The recipe IS the decomposition: run_prepared spawns one worker per topic
    and never calls the decompose stage."""
    from tests.test_hierarchy import MID, WORKER_SYSTEM, FakeBackend, _orchestrator

    backend = FakeBackend()
    orchestrator = _orchestrator(backend, tmp_path)
    recipe = BriefRecipe(topics=["alpha", "beta"])
    result = orchestrator.run_prepared(brief_task(recipe), specs_from_brief(recipe))
    assert result.fell_back is False
    assert len(result.envelopes) == 2
    assert not any("Split the user's task" in c["system"] for c in backend.calls)
    worker_calls = [c for c in backend.calls if c["system"] == WORKER_SYSTEM]
    assert len(worker_calls) == 2
    assert all(c["model"] == MID for c in worker_calls)
