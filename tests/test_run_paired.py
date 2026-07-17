"""Offline test of the paired-experiment runner core (M15-C1) — no network, injected solve/grade."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parents[1] / "bench" / "local_lift" / "run_paired.py"


def _load_runner():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("run_paired", _RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tasks() -> list[dict]:
    # Minimal task dicts — the runner only needs id/files/test to restore a workspace.
    return [
        {"id": f"t{i}", "prompt": "p", "files": {"a.py": "x = 0"}, "test": "test_a.py", "test_src": "def test(): pass"}
        for i in range(6)
    ]


def test_runner_restores_and_pairs_outcomes(tmp_path: Path) -> None:
    runner = _load_runner()
    tasks = _tasks()

    restored: list[str] = []

    def solve(task: dict, ws: Path, arm: str) -> None:
        # The workspace must have been freshly restored before this arm runs.
        assert (ws / "a.py").exists()
        restored.append(f"{task['id']}:{arm}")

    # Baseline fails the first two tasks; chimera fixes them → chimera wins 2 discordant pairs.
    hard = {"t0", "t1"}

    def grade(task: dict, ws: Path) -> bool:
        # Distinguish arms by which file the fake solver would have written — here just by policy:
        # baseline fails `hard`, chimera passes everything.
        return not (task["id"] in hard and _current_arm[0] == "baseline")

    # Track the current arm via the solve hook (grade has no arm param, mirroring the real pytest).
    _current_arm = [""]
    _orig = solve

    def solve_tracking(task: dict, ws: Path, arm: str) -> None:
        _current_arm[0] = arm
        _orig(task, ws, arm)

    result, rows = runner.run_paired(tasks, solve=solve_tracking, grade=grade, root=tmp_path)

    # Restore happened before EACH arm of EACH task (2 arms × 6 tasks = 12 solves).
    assert len(restored) == 12
    assert result.treatment_only == 2 and result.baseline_only == 0  # chimera recovered t0, t1
    assert result.delta > 0
    assert len(rows) == 12


def test_finished_pair_is_replayed_from_the_journal_and_never_re_run(tmp_path: Path) -> None:
    """A journalled pair must not touch the solver again — re-running it is how you'd roll for a pass."""
    runner = _load_runner()
    journal = tmp_path / "journal.jsonl"
    # t0 finished in an earlier process: baseline failed, chimera passed.
    journal.write_text(
        json.dumps({"task": "t0", "model": "m", "baseline": False, "chimera": True}) + "\n",
        encoding="utf-8",
    )

    calls: list[str] = []

    def solve(task: dict, ws: Path, arm: str) -> None:
        calls.append(f"{task['id']}:{arm}")

    # grade() says PASS for anything actually run — so if t0 were re-run, its baseline would flip to
    # pass and the discordant pair would vanish. The recorded outcome is what must survive.
    def grade(task: dict, ws: Path) -> bool:
        return True

    result, rows = runner.run_paired(
        _tasks()[:2], solve=solve, grade=grade, root=tmp_path / "ws", journal=journal, model="m"
    )

    assert calls == ["t1:baseline", "t1:chimera"], "the finished pair must not reach the solver"
    assert result.treatment_only == 1, "the journalled outcome must win, not a fresh grade()"
    assert {r["task"] for r in rows} == {"t0", "t1"}, "a resumed pair still lands in the reported grid"


def test_interrupted_pair_is_discarded_whole_and_both_arms_replay(tmp_path: Path) -> None:
    """A crash between the arms must leave nothing behind — no half pair, no kept baseline."""
    runner = _load_runner()
    journal = tmp_path / "journal.jsonl"
    task = _tasks()[:1]
    calls: list[str] = []

    def dying_solve(t: dict, ws: Path, arm: str) -> None:
        calls.append(arm)
        if arm == "chimera":
            raise RuntimeError("process died mid-pair")

    with pytest.raises(RuntimeError):
        runner.run_paired(
            task, solve=dying_solve, grade=lambda t, ws: True,
            root=tmp_path / "ws", journal=journal, model="m",
        )
    assert calls == ["baseline", "chimera"]
    assert not journal.exists(), "a pair is only journalled once BOTH arms are in"

    # Resuming replays the pair from scratch — the orphaned baseline is not reused.
    calls.clear()
    runner.run_paired(
        task, solve=lambda t, ws, arm: calls.append(arm), grade=lambda t, ws: True,
        root=tmp_path / "ws", journal=journal, model="m",
    )
    assert calls == ["baseline", "chimera"], "both arms replay from the fresh restore"
    assert len(journal.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_journal_from_a_different_model_is_ignored(tmp_path: Path) -> None:
    """A cached cell is only a valid resume under the same conditions that produced it."""
    runner = _load_runner()
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps({"task": "t0", "model": "some-other-model", "baseline": False, "chimera": True}) + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    runner.run_paired(
        _tasks()[:1], solve=lambda t, ws, arm: calls.append(arm), grade=lambda t, ws: True,
        root=tmp_path / "ws", journal=journal, model="m",
    )
    assert calls == ["baseline", "chimera"], "another model's record must not be replayed as ours"


def test_fresh_workspace_wipes_prior_arm_edits(tmp_path: Path) -> None:
    runner = _load_runner()
    task = _tasks()[0]
    ws = runner._fresh_workspace(task, tmp_path)
    (ws / "leftover.py").write_text("from a previous arm", encoding="utf-8")
    # Restoring again must remove the previous arm's edits — both arms start identical.
    ws2 = runner._fresh_workspace(task, tmp_path)
    assert not (ws2 / "leftover.py").exists()
    assert (ws2 / "a.py").read_text(encoding="utf-8") == "x = 0"
