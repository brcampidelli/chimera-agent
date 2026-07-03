"""Tests for the SkillCoach process signal + trajectory process filter (no network)."""

from __future__ import annotations

from typing import Any

from chimera.ecosystem.events import events_from_transcript, step_following_score
from chimera.ecosystem.evolution import CurationConfig, curate_dpo, curate_sft
from chimera.ecosystem.trajectory import Outcome, Trajectory

_OK = [{"tool": "t", "ok": True}]
_MIXED = [{"tool": "t", "ok": True}, {"tool": "t", "ok": False}]


def _traj(seq: int, prompt: str, resp: str, outcome: Outcome, reward: float, events: list) -> Trajectory:
    return Trajectory(seq=seq, prompt=prompt, response=resp, outcome=outcome, reward=reward, events=events)


def test_events_from_transcript_pairs_tools_and_flags_errors() -> None:
    transcript: list[dict[str, Any]] = [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
        {"role": "assistant", "tool_calls": [{"id": "c2", "function": {"name": "run_shell"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "error: boom"},
    ]
    events = events_from_transcript(transcript)
    assert events == [{"tool": "read_file", "ok": True}, {"tool": "run_shell", "ok": False}]
    assert step_following_score(events) == 0.5


def test_step_following_empty_is_one() -> None:
    assert step_following_score([]) == 1.0  # a pure-reasoning answer is not penalized


def test_process_score_on_trajectory() -> None:
    assert _traj(0, "p", "r", "success", 1.0, _MIXED).process_score() == 0.5


def test_curate_sft_process_filter() -> None:
    clean = _traj(0, "p1", "good", "success", 1.0, _OK)  # score 1.0
    sloppy = _traj(1, "p2", "meh", "success", 1.0, _MIXED)  # score 0.5
    assert len(curate_sft([clean, sloppy], CurationConfig())) == 2  # off by default
    rows = curate_sft([clean, sloppy], CurationConfig(min_process=0.6))  # sloppy dropped
    assert len(rows) == 1 and rows[0]["messages"][0]["content"] == "p1"


def test_curate_dpo_process_filter_on_chosen() -> None:
    clean = _traj(0, "p", "good", "success", 1.0, _OK)
    sloppy = _traj(1, "p", "meh", "success", 0.9, _MIXED)  # higher-noise success, same prompt
    fail = _traj(2, "p", "bad", "failure", 0.0, [])
    rows = curate_dpo([sloppy, clean, fail], CurationConfig(min_process=0.6))
    assert len(rows) == 1 and rows[0]["chosen"] == "good" and rows[0]["rejected"] == "bad"


def test_backward_compat_old_trajectory_json() -> None:
    old = '{"seq":0,"prompt":"p","response":"r","outcome":"success","reward":1.0,"steps":0}'
    traj = Trajectory.model_validate_json(old)
    assert traj.events == [] and traj.process_score() == 1.0
