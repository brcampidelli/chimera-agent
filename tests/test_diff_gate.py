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


def test_an_empty_added_file_does_not_hide_later_real_adds() -> None:
    # The empty-file skip must not abandon the rest of the scan: "a_empty.py" sorts FIRST, so a
    # `break` here would swallow the real edit behind it and read the step as unproductive.
    before = _snap({})
    after = _snap({"a_empty.py": "  \n\n", "b_real.py": "x = 1"})
    d = diff_snapshots(before, after)
    assert d.added == ["b_real.py"]
    assert d.is_productive is True


def test_a_file_that_turns_binary_is_skipped_not_crashed_on() -> None:
    # Text before, binary after: with no content on one side there is nothing to compare, so the
    # path is skipped rather than normalized (which would blow up on the missing side).
    before = _snap({"f.dat": "text"})
    after = _snap({}, binaries={"f.dat"})
    d = diff_snapshots(before, after)
    assert d.modified == []
    assert d.is_productive is False


def test_a_binary_file_does_not_hide_later_modified_files() -> None:
    # "a_img.png" (binary, unjudgeable) sorts before "z_text.py" — the scan must continue past it.
    before = _snap({"z_text.py": "old"}, binaries={"a_img.png"})
    after = _snap({"z_text.py": "new"}, binaries={"a_img.png"})
    assert diff_snapshots(before, after).modified == ["z_text.py"]


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


def test_the_patch_header_names_the_file_on_both_sides() -> None:
    # The header is what the Code screen labels the patch with. `lineterm=""` keeps the header lines
    # bare, so the rendered patch has no stray blank lines between them.
    diffs = unified_diffs(_snap({"m.py": "old"}), _snap({"m.py": "new"}))
    lines = diffs[0].patch.split("\n")
    assert lines[0] == "--- m.py"
    assert lines[1] == "+++ m.py"


def test_an_added_file_diffs_against_empty_so_every_body_line_is_an_addition() -> None:
    diffs = unified_diffs(_snap({}), _snap({"new.py": "a = 1\nb = 2"}))
    body = [
        line
        for line in diffs[0].patch.split("\n")
        if line[:1] in "+-" and not line.startswith(("---", "+++"))
    ]
    assert body == ["+a = 1", "+b = 2"]  # nothing removed: it diffs against ""


def test_a_removed_file_diffs_against_empty_so_every_body_line_is_a_deletion() -> None:
    diffs = unified_diffs(_snap({"gone.py": "a = 1"}), _snap({}))
    body = [
        line
        for line in diffs[0].patch.split("\n")
        if line[:1] in "+-" and not line.startswith(("---", "+++"))
    ]
    assert body == ["-a = 1"]


def test_unified_diffs_caps_at_twenty_files_by_default() -> None:
    after = _snap({f"f{i:02d}.py": f"x = {i}" for i in range(25)})
    diffs = unified_diffs(_snap({}), after)
    assert len(diffs) == 20  # the documented default bound
    assert [d.path for d in diffs] == [f"f{i:02d}.py" for i in range(20)]  # sorted → deterministic


def test_unified_diffs_truncates_at_the_default_char_bound() -> None:
    big_after = _snap({"big.py": "\n".join(f"line {i}" for i in range(2000))})
    diffs = unified_diffs(_snap({}), big_after)
    marker = "\n… [diff truncated]"
    assert diffs[0].truncated is True
    assert diffs[0].patch.endswith(marker)
    assert len(diffs[0].patch) == 4000 + len(marker)  # clipped at the documented 4000-char default


def test_a_patch_exactly_at_the_char_bound_is_not_truncated() -> None:
    # The boundary is `> max_chars`, not `>=`: a patch that exactly fits is complete.
    before, after = _snap({"a.py": ""}), _snap({"a.py": "x = 1"})
    exact = len(unified_diffs(before, after, max_chars=10_000)[0].patch)
    diffs = unified_diffs(before, after, max_chars=exact)
    assert diffs[0].truncated is False
    assert not diffs[0].patch.endswith("[diff truncated]")


def test_a_binary_note_is_untruncated_and_does_not_stop_the_scan() -> None:
    # "b.png" (binary) sorts before "z.py", so a `break` on the binary branch would drop the real
    # patch behind it.
    before = _snap({"z.py": "old"}, binaries={"a.png"})
    after = _snap({"z.py": "new"}, binaries={"a.png", "b.png"})
    by_path = {d.path: d for d in unified_diffs(before, after)}
    assert by_path["b.png"].patch == "(binary or non-text file: b.png)"
    assert by_path["b.png"].truncated is False  # a note is not a clipped patch
    assert by_path["z.py"].patch.startswith("--- z.py")  # the scan continued past the binary


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
