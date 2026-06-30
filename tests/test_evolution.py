"""Tests for opt-in model evolution: curation, readiness, recipe (no network)."""

from __future__ import annotations

from pathlib import Path

from chimera.ecosystem import (
    CurationConfig,
    TrajectoryCollector,
    assess,
    curate_dpo,
    curate_sft,
    write_recipe,
)


def _collector(tmp_path: Path) -> TrajectoryCollector:
    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    collector.record("task A", "good answer A", outcome="success", reward=1.0)
    collector.record("task A", "bad answer A", outcome="failure", reward=0.0)
    collector.record("task B", "good answer B", outcome="success", reward=0.8)
    collector.record("task A", "good answer A", outcome="success", reward=1.0)  # duplicate
    collector.record("noise", "meh", outcome="unknown", reward=0.0)
    return collector


def test_curate_sft_keeps_unique_successes(tmp_path: Path) -> None:
    rows = curate_sft(_collector(tmp_path).all(), CurationConfig(dedup=True))
    contents = [row["messages"][1]["content"] for row in rows]
    assert "good answer A" in contents and "good answer B" in contents
    assert len(rows) == 2  # duplicate A collapsed; failure + unknown dropped


def test_curate_sft_min_reward(tmp_path: Path) -> None:
    rows = curate_sft(_collector(tmp_path).all(), CurationConfig(min_reward=0.9))
    assert len(rows) == 1  # only reward >= 0.9 (task A); B at 0.8 drops


def test_curate_dpo_pairs_success_against_failure(tmp_path: Path) -> None:
    rows = curate_dpo(_collector(tmp_path).all(), CurationConfig())
    assert len(rows) == 1  # task A has both; B has no failure
    assert rows[0] == {"prompt": "task A", "chosen": "good answer A", "rejected": "bad answer A"}


def test_curate_dpo_respects_reward_margin(tmp_path: Path) -> None:
    rows = curate_dpo(_collector(tmp_path).all(), CurationConfig(min_margin=2.0))
    assert rows == []  # margin 1.0 - 0.0 does not exceed 2.0


def test_assess_reports_counts_and_readiness(tmp_path: Path) -> None:
    readiness = assess(_collector(tmp_path), min_examples=2)
    assert readiness.successes == 3
    assert readiness.failures == 1
    assert readiness.sft_examples == 2
    assert readiness.dpo_pairs == 1
    assert readiness.ready is True


def test_write_recipe_emits_runnable_files(tmp_path: Path) -> None:
    files = write_recipe(tmp_path / "recipe", base_model="some/model", fmt="dpo", dataset="d.jsonl")
    assert {f.name for f in files} == {"train.py", "requirements.txt", "README.md"}
    train = (tmp_path / "recipe" / "train.py").read_text(encoding="utf-8")
    assert "DPOTrainer" in train
    assert "some/model" in train
