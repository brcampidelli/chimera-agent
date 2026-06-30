"""Tests for the Tier-4 ecosystem: trajectories, change queue, meta-agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.ecosystem import (
    AgentBlueprint,
    Change,
    ChangeQueue,
    MetaAgent,
    TrajectoryCollector,
)
from chimera.providers import CompletionResult

# --- trajectories -----------------------------------------------------------

def test_trajectory_record_and_persist(tmp_path: Path) -> None:
    path = tmp_path / "traj.jsonl"
    collector = TrajectoryCollector(path)
    collector.record("p1", "r1", outcome="success")
    collector.record("p2", "r2", outcome="failure")

    reopened = TrajectoryCollector(path)
    assert len(reopened) == 2
    assert reopened.all()[0].outcome == "success"


def test_export_sft_only_successes(tmp_path: Path) -> None:
    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    collector.record("p", "good", outcome="success")
    collector.record("p", "bad", outcome="failure")

    out = tmp_path / "sft.jsonl"
    count = collector.export_sft(out)
    assert count == 1
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["messages"][1]["content"] == "good"


def test_export_dpo_pairs(tmp_path: Path) -> None:
    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    collector.record("same prompt", "the good one", outcome="success")
    collector.record("same prompt", "the bad one", outcome="failure")
    collector.record("lonely", "only success", outcome="success")  # no pair

    out = tmp_path / "dpo.jsonl"
    count = collector.export_dpo(out)
    assert count == 1
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["chosen"] == "the good one"
    assert row["rejected"] == "the bad one"


# --- change queue -----------------------------------------------------------

def test_change_queue_batches() -> None:
    queue = ChangeQueue(batch_size=2)
    for i in range(5):
        queue.submit(Change(id=str(i), description=f"change {i}"))
    assert queue.pending() == 5

    batch = queue.merge_batch()
    assert len(batch) == 2
    assert len(queue.merged) == 2
    assert queue.pending() == 3


def test_change_queue_drain() -> None:
    queue = ChangeQueue(batch_size=2)
    for i in range(5):
        queue.submit(Change(id=str(i), description="c"))
    batches = queue.drain()
    assert [len(b) for b in batches] == [2, 2, 1]
    assert len(queue.merged) == 5
    assert queue.pending() == 0


def test_change_queue_invalid_batch_size() -> None:
    with pytest.raises(ValueError):
        ChangeQueue(batch_size=0)


# --- meta-agent -------------------------------------------------------------

class DesignBackend:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content=self.content, model="fake")


def test_meta_agent_designs_and_filters_tools() -> None:
    backend = DesignBackend(
        '{"name": "researcher", "role_prompt": "You research.", "tools": ["read_file", "evil_tool"]}'
    )
    meta = MetaAgent(backend, allowed_tools=["read_file", "http_get"])
    blueprint = meta.design("gather facts")
    assert blueprint is not None
    assert blueprint.name == "researcher"
    assert blueprint.tools == ["read_file"]  # disallowed 'evil_tool' filtered out


def test_meta_agent_design_unparseable() -> None:
    assert MetaAgent(DesignBackend("not json"), allowed_tools=[]).design("t") is None


def test_meta_agent_builds_role_agent() -> None:
    meta = MetaAgent(DesignBackend("{}"), allowed_tools=[])
    blueprint = AgentBlueprint(name="writer", role_prompt="You write.")
    agent = meta.build(blueprint)
    assert agent.name == "writer"
    assert agent.role.system_prompt == "You write."


def test_meta_agent_detects_reward_hacking() -> None:
    blueprint = AgentBlueprint(name="x", role_prompt="p")
    report = MetaAgent.evaluate(
        blueprint,
        run=lambda bp: "passes VISIBLE only",
        visible_check=lambda o: "VISIBLE" in o,
        hidden_check=lambda o: "HIDDEN" in o,
    )
    assert report.reward_hacking_suspected is True
    assert report.genuine_success is False


def test_meta_agent_genuine_success() -> None:
    report = MetaAgent.evaluate(
        AgentBlueprint(name="x", role_prompt="p"),
        run=lambda bp: "VISIBLE and HIDDEN both pass",
        visible_check=lambda o: "VISIBLE" in o,
        hidden_check=lambda o: "HIDDEN" in o,
    )
    assert report.genuine_success is True
    assert report.reward_hacking_suspected is False
