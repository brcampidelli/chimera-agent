"""Tests for the rejection-sampling fine-tuning loop, bench-gated (M14 C3)."""

from __future__ import annotations

from pathlib import Path

from chimera.ecosystem.loop import (
    RejectionSamplingLoop,
    StaticEvaluator,
    rejection_sample,
    run_rft,
)
from chimera.ecosystem.trajectory import Trajectory, TrajectoryCollector


def _traj(prompt: str, response: str, *, outcome: str = "success", reward: float = 1.0, seq: int = 0) -> Trajectory:
    return Trajectory(seq=seq, prompt=prompt, response=response, outcome=outcome, reward=reward)


# --- rejection sampling ------------------------------------------------------------------


def test_rejection_keeps_only_high_reward_successes() -> None:
    trajs = [
        _traj("a", "good", reward=0.9),
        _traj("b", "meh", reward=0.2),  # below min_reward
        _traj("c", "failed", outcome="failure", reward=1.0),  # not a success
    ]
    rs = rejection_sample(trajs, min_reward=0.5)
    assert [t.prompt for t in rs.accepted] == ["a"]
    assert rs.total == 3
    assert rs.accept_rate == 1 / 3


def test_rejection_top_k_per_prompt_caps_easy_tasks() -> None:
    trajs = [
        _traj("same", "r1", reward=0.9),
        _traj("same", "r2", reward=0.8),
        _traj("same", "r3", reward=0.7),
        _traj("other", "r4", reward=0.9),
    ]
    rs = rejection_sample(trajs, min_reward=0.5, top_k_per_prompt=2)
    assert rs.per_prompt == {"same": 2, "other": 1}  # 'same' capped at its 2 best
    assert len(rs.accepted) == 3


def test_rejection_process_filter() -> None:
    clean = _traj("a", "r", reward=0.9)
    # A trajectory with a failed tool step scores <1.0 on process; filter it out.
    sloppy = Trajectory(
        seq=1, prompt="b", response="r", outcome="success", reward=0.9,
        events=[{"tool": "shell", "ok": False}],
    )
    rs = rejection_sample([clean, sloppy], min_reward=0.5, min_process=1.0)
    assert [t.prompt for t in rs.accepted] == ["a"]


def test_empty_accept_rate_is_zero() -> None:
    assert rejection_sample([], min_reward=0.5).accept_rate == 0.0


# --- the bench gate ----------------------------------------------------------------------


def _collector_with(n: int, tmp_path: Path) -> TrajectoryCollector:
    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    for i in range(n):
        collector.record(f"task-{i}", "answer", outcome="success", reward=1.0)
    return collector


def test_round_withheld_when_not_enough_signal(tmp_path: Path) -> None:
    collector = _collector_with(3, tmp_path)
    round_result = run_rft(collector, [True] * 20, [True] * 20, min_examples=30)
    assert round_result.ready is False
    assert round_result.promoted is False
    assert round_result.ab is None  # never even ran the A/B — no signal to gate
    assert "insufficient" in round_result.reason


def test_round_promoted_when_candidate_beats_baseline(tmp_path: Path) -> None:
    collector = _collector_with(30, tmp_path)
    # Candidate clearly better: 28/30 vs 12/30 -> difference CI excludes 0.
    baseline = [True] * 12 + [False] * 18
    candidate = [True] * 28 + [False] * 2
    round_result = run_rft(collector, baseline, candidate, min_examples=30)
    assert round_result.ready is True
    assert round_result.promoted is True
    assert round_result.ab is not None and round_result.ab.significant is True
    assert "promote" in round_result.reason


def test_round_withheld_when_lift_not_significant(tmp_path: Path) -> None:
    collector = _collector_with(30, tmp_path)
    # A tiny edge on small n: CI includes 0, so the round is withheld (don't train on noise).
    baseline = [True] * 15 + [False] * 15
    candidate = [True] * 16 + [False] * 14
    round_result = run_rft(collector, baseline, candidate, min_examples=30)
    assert round_result.ready is True
    assert round_result.promoted is False
    assert "noise" in round_result.reason


def test_transfer_holdout_blocks_negative_transfer(tmp_path: Path) -> None:
    """A round that wins the tuned bench but significantly REGRESSES a same-capability
    holdout is negative transfer (EvoAgentBench) — withheld despite the tuned win."""
    collector = _collector_with(30, tmp_path)
    loop = RejectionSamplingLoop(
        collector,
        StaticEvaluator(
            [True] * 12 + [False] * 18, [True] * 28 + [False] * 2,  # tuned: clear win
            baseline_holdout=[True] * 28 + [False] * 2,             # holdout: 93% ...
            candidate_holdout=[True] * 10 + [False] * 20,           # ... -> 33%, big regression
        ),
        min_examples=30,
    )
    round_result = loop.run()
    assert round_result.ready is True
    assert round_result.transfer_measured is True
    assert round_result.promoted is False
    assert "NEGATIVE TRANSFER" in round_result.reason


def test_transfer_holdout_promotes_when_it_generalizes(tmp_path: Path) -> None:
    collector = _collector_with(30, tmp_path)
    loop = RejectionSamplingLoop(
        collector,
        StaticEvaluator(
            [True] * 12 + [False] * 18, [True] * 28 + [False] * 2,   # tuned: win
            baseline_holdout=[True] * 15 + [False] * 15,             # holdout: parity, no regression
            candidate_holdout=[True] * 16 + [False] * 14,
        ),
        min_examples=30,
    )
    round_result = loop.run()
    assert round_result.transfer_measured is True
    assert round_result.promoted is True


# --- export gate -------------------------------------------------------------------------


def test_export_withheld_for_unpromoted_round(tmp_path: Path) -> None:
    collector = _collector_with(30, tmp_path)
    loop = RejectionSamplingLoop(
        collector, StaticEvaluator([True] * 15 + [False] * 15, [True] * 16 + [False] * 14),
        min_examples=30,
    )
    round_result = loop.run()
    assert round_result.promoted is False
    assert loop.export(round_result, tmp_path / "out") == []  # nothing written
    assert not (tmp_path / "out" / "dataset.jsonl").exists()


def test_export_writes_dataset_and_recipe_when_promoted(tmp_path: Path) -> None:
    collector = _collector_with(30, tmp_path)
    loop = RejectionSamplingLoop(
        collector, StaticEvaluator([True] * 12 + [False] * 18, [True] * 28 + [False] * 2),
        min_examples=30,
    )
    round_result = loop.run()
    assert round_result.promoted is True
    written = loop.export(round_result, tmp_path / "out")
    names = {p.name for p in written}
    assert {"dataset.jsonl", "train.py", "requirements.txt", "README.md"} <= names
    assert (tmp_path / "out" / "dataset.jsonl").read_text(encoding="utf-8").strip()  # non-empty


def test_export_force_overrides_the_gate(tmp_path: Path) -> None:
    collector = _collector_with(30, tmp_path)
    loop = RejectionSamplingLoop(
        collector, StaticEvaluator([True] * 15 + [False] * 15, [True] * 16 + [False] * 14),
        min_examples=30,
    )
    round_result = loop.run()
    written = loop.export(round_result, tmp_path / "out", force=True)
    assert written  # --force bypasses the promote gate deliberately


def test_static_evaluator_unknown_arm_is_empty() -> None:
    assert StaticEvaluator([True], [False]).evaluate("mystery") == []


def test_round_summary_includes_ab_when_present(tmp_path: Path) -> None:
    collector = _collector_with(30, tmp_path)
    round_result = run_rft(collector, [True] * 12 + [False] * 18, [True] * 28 + [False] * 2, min_examples=30)
    summary = round_result.summary()
    assert summary["promoted"] is True
    assert "ab" in summary and summary["ab"]["significant"] is True  # type: ignore[index]
