"""Tests for the diff-gate (M15-A1): certify an evolution step by its real diff, not self-report."""

from __future__ import annotations

from pathlib import Path

from chimera.core.checkpoint import FileSnapshot
from chimera.ecosystem.loop import rejection_sample, run_rft
from chimera.ecosystem.trajectory import Trajectory, TrajectoryCollector
from chimera.evolution.diff_gate import ProductiveDiff, diff_snapshots, unified_diffs


def _snap(files: dict[str, str], *, binaries: set[str] | None = None) -> FileSnapshot:
    """Build a FileSnapshot: text files carry content; binaries are present-only."""
    present = set(files) | (binaries or set())
    return FileSnapshot(files=dict(files), present=present)


# --- diff classification -----------------------------------------------------------------


def test_added_removed_modified_are_classified() -> None:
    before = _snap({"keep.py": "x = 1", "gone.py": "y = 2", "same.py": "z = 3"})
    after = _snap({"keep.py": "x = 99", "same.py": "z = 3", "new.py": "w = 4"})
    d = diff_snapshots(before, after)
    assert d.added == ["new.py"]
    assert d.removed == ["gone.py"]
    assert d.modified == ["keep.py"]
    assert d.is_productive is True


def test_no_change_is_not_productive() -> None:
    snap = _snap({"a.py": "value = 1"})
    d = diff_snapshots(snap, _snap({"a.py": "value = 1"}))
    assert d.is_productive is False
    assert d.audit_summary() == "diff: no productive change"


def test_whitespace_only_change_is_not_productive() -> None:
    before = _snap({"a.py": "def f():\n    return 1"})
    after = _snap({"a.py": "def f():   \n    return 1\n\n"})  # trailing ws + blank lines only
    assert diff_snapshots(before, after).is_productive is False


def test_touched_empty_file_is_not_a_productive_add() -> None:
    before = _snap({})
    after = _snap({"empty.py": "  \n\n"})  # created but no real content
    assert diff_snapshots(before, after).is_productive is False


def test_binary_add_counts_but_binary_content_change_cannot() -> None:
    # A new binary file (present, no text) is a real change.
    assert diff_snapshots(_snap({}), _snap({}, binaries={"img.png"})).added == ["img.png"]
    # A binary present on both sides can't be judged as modified (no content to compare).
    both = _snap({"a.py": "x"}, binaries={"img.png"})
    assert diff_snapshots(both, both).is_productive is False


def test_audit_summary_is_machine_derived() -> None:
    before = _snap({"gone.py": "1"})
    after = _snap({"a.py": "new", "b.py": "new"})
    summary = diff_snapshots(before, after).audit_summary()
    assert "+2 new" in summary and "-1 removed" in summary
    assert "a.py" in summary and "b.py" in summary  # names come from the diff, not a narrative


def test_audit_summary_caps_the_file_list() -> None:
    after = _snap({f"f{i}.py": "content" for i in range(10)})
    summary = diff_snapshots(_snap({}), after).audit_summary(max_files=3)
    assert "+7 more" in summary


def test_empty_diff_is_not_productive() -> None:
    assert ProductiveDiff().is_productive is False


# --- unified per-file diffs (the Code screen's real patch) --------------------------------


def test_unified_diff_of_a_modified_file_has_real_hunk_and_pm_lines() -> None:
    before = _snap({"a.py": "x = 1\ny = 2\n"})
    after = _snap({"a.py": "x = 1\ny = 99\n"})
    diffs = unified_diffs(before, after)
    assert [d.path for d in diffs] == ["a.py"]
    patch = diffs[0].patch
    assert "@@" in patch  # a real hunk header
    assert "-y = 2" in patch and "+y = 99" in patch
    assert diffs[0].truncated is False


def test_unified_diff_of_a_new_file_is_all_added_lines() -> None:
    diffs = unified_diffs(_snap({}), _snap({"new.py": "line1\nline2"}))
    patch = diffs[0].patch
    assert "+line1" in patch and "+line2" in patch
    assert "-line1" not in patch  # a fresh file has no removed lines


def test_unified_diff_of_a_removed_file_is_all_removed_lines() -> None:
    diffs = unified_diffs(_snap({"gone.py": "a\nb"}), _snap({}))
    patch = diffs[0].patch
    assert "-a" in patch and "-b" in patch
    assert "+a" not in patch


def test_unified_diffs_caps_the_number_of_files() -> None:
    after = _snap({f"f{i}.py": f"content {i}" for i in range(30)})
    diffs = unified_diffs(_snap({}), after, max_files=5)
    assert len(diffs) == 5
    assert [d.path for d in diffs] == ["f0.py", "f1.py", "f10.py", "f11.py", "f12.py"]  # sorted


def test_unified_diff_truncates_a_large_patch() -> None:
    big_after = _snap({"big.py": "\n".join(f"line {i}" for i in range(2000))})
    diffs = unified_diffs(_snap({}), big_after, max_chars=200)
    assert diffs[0].truncated is True
    assert "[diff truncated]" in diffs[0].patch


def test_unified_diff_of_a_binary_add_is_a_note_not_a_crash() -> None:
    diffs = unified_diffs(_snap({}), _snap({}, binaries={"img.png"}))
    assert diffs[0].path == "img.png"
    assert "binary" in diffs[0].patch.lower()


def test_unified_diffs_skips_whitespace_only_change() -> None:
    before = _snap({"a.py": "def f():\n    return 1"})
    after = _snap({"a.py": "def f():   \n    return 1\n\n"})  # whitespace only
    assert unified_diffs(before, after) == []


# --- the rejection-sampling gate ---------------------------------------------------------


def _traj(prompt: str, *, diff_productive: bool | None, seq: int = 0) -> Trajectory:
    return Trajectory(
        seq=seq, prompt=prompt, response="answer", outcome="success", reward=1.0,
        diff_productive=diff_productive,
    )


def test_require_productive_diff_rejects_hollow_success() -> None:
    trajs = [
        _traj("real", diff_productive=True, seq=0),
        _traj("hollow", diff_productive=False, seq=1),  # "success" that changed nothing
        _traj("untracked", diff_productive=None, seq=2),  # no guard: can't certify
    ]
    # Off (default): all three high-reward successes are kept.
    assert len(rejection_sample(trajs, min_reward=0.5).accepted) == 3
    # On: only the diff-certified one survives — self-reported no-op successes are dropped.
    gated = rejection_sample(trajs, min_reward=0.5, require_productive_diff=True)
    assert [t.prompt for t in gated.accepted] == ["real"]


def test_require_productive_diff_threads_through_run_rft(tmp_path: Path) -> None:
    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    for i in range(30):
        collector.record(f"task-{i}", "answer", outcome="success", reward=1.0, diff_productive=False)
    # Every success is a no-op diff → with the gate on, zero accepted → round not ready.
    round_result = run_rft(
        collector, [True] * 30, [True] * 30, min_examples=30, require_productive_diff=True
    )
    assert round_result.ready is False
    assert round_result.accepted_examples == 0


# --- trajectory persistence carries the machine audit ------------------------------------


def test_trajectory_persists_diff_fields(tmp_path: Path) -> None:
    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    collector.record(
        "task", "answer", outcome="success", reward=1.0,
        diff_productive=True, diff_summary="diff: +1 new, ~0 changed, -0 removed (a.py)",
    )
    reloaded = TrajectoryCollector(tmp_path / "traj.jsonl")  # round-trips through JSONL
    item = reloaded.all()[0]
    assert item.diff_productive is True
    assert "a.py" in (item.diff_summary or "")
