"""Offline test of the paired-experiment runner core (M15-C1) — no network, injected solve/grade."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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


def test_fresh_workspace_wipes_prior_arm_edits(tmp_path: Path) -> None:
    runner = _load_runner()
    task = _tasks()[0]
    ws = runner._fresh_workspace(task, tmp_path)
    (ws / "leftover.py").write_text("from a previous arm", encoding="utf-8")
    # Restoring again must remove the previous arm's edits — both arms start identical.
    ws2 = runner._fresh_workspace(task, tmp_path)
    assert not (ws2 / "leftover.py").exists()
    assert (ws2 / "a.py").read_text(encoding="utf-8") == "x = 0"
