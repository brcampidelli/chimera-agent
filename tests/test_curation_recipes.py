"""Tests for the Data-Recipes curation heuristics (long-horizon, diversity)."""

from __future__ import annotations

from chimera.ecosystem import CurationConfig, curate_sft
from chimera.ecosystem.trajectory import Trajectory


def _traj(seq: int, prompt: str, response: str, *, reward: float = 1.0, steps: int = 0) -> Trajectory:
    return Trajectory(seq=seq, prompt=prompt, response=response, outcome="success", reward=reward, steps=steps)


def test_long_horizon_filter_keeps_only_deep_traces() -> None:
    trajs = [_traj(0, "a", "r1", steps=2), _traj(1, "b", "r2", steps=6)]
    rows = curate_sft(trajs, CurationConfig(min_steps=5))
    assert len(rows) == 1
    assert rows[0]["messages"][0]["content"] == "b"


def test_diversity_cap_keeps_one_example_per_task() -> None:
    trajs = [
        _traj(0, "same task", "r1", reward=1.0),
        _traj(1, "same task", "r2", reward=0.9),
        _traj(2, "other task", "r3", reward=1.0),
    ]
    rows = curate_sft(trajs, CurationConfig(max_per_prompt=1))
    contents = [r["messages"][1]["content"] for r in rows]
    assert len(rows) == 2
    assert "r1" in contents and "r3" in contents  # best-reward per task
    assert "r2" not in contents  # the duplicate task is capped


def test_defaults_keep_current_behaviour() -> None:
    trajs = [_traj(0, "a", "r1", steps=0), _traj(1, "a", "r2", steps=0)]
    assert len(curate_sft(trajs)) == 2  # no min_steps / cap by default
