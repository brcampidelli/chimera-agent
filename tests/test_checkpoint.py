"""Tests for workspace snapshot/restore and verifiers."""

from __future__ import annotations

from pathlib import Path

from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.verify import CommandVerifier, NullVerifier


def test_restore_reverts_changes_and_deletes_new(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("original", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()

    (tmp_path / "a.txt").write_text("modified", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new file", encoding="utf-8")

    changes = guard.restore(snap)
    assert changes == 2
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "original"
    assert not (tmp_path / "b.txt").exists()


def test_truncated_snapshot_does_not_delete_uncaptured_files(tmp_path: Path) -> None:
    # With more files than the cap, the snapshot is truncated. restore() must NOT delete the
    # uncaptured pre-existing files (they'd look "new") — that would be data loss.
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(f"content {i}", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path, max_files=2)  # capture only 2 of the 5
    snap = guard.snapshot()
    assert len(snap.present) == 2

    guard.restore(snap)
    survivors = sorted(p.name for p in tmp_path.glob("*.txt"))
    assert survivors == ["f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt"]  # none deleted


def test_repo_root_workspace_is_never_delete_pruned(tmp_path: Path) -> None:
    # If the workspace is a real git repo root, restore() must NOT run the delete-new pass — even for
    # files genuinely absent from the snapshot. verify-or-revert's cleanup is for throwaway task
    # workspaces; pointing it at a repo (a path/config bug did, 2026-07-17) would let a revert wipe the
    # user's tracked and untracked files. A `.git` at the root is the signal.
    (tmp_path / ".git").mkdir()  # marks tmp_path as a repository root
    (tmp_path / "tracked.py").write_text("committed = True", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()  # snapshot does NOT contain a file that will appear later

    (tmp_path / "appeared_after.py").write_text("x = 1", encoding="utf-8")

    guard.restore(snap)
    # The would-be-"new" file survives: the delete pass was skipped because this is a repo root.
    assert (tmp_path / "appeared_after.py").exists()
    assert (tmp_path / "tracked.py").exists()


def test_repo_subdirectory_is_also_guarded(tmp_path: Path) -> None:
    # REGRESSION (adversarial review 2026-07-18): the guard checked only for `.git` at the workspace
    # ROOT, so a workspace pointed at a SUBDIR of a repo (.git at an ancestor) still ran the delete
    # pass and wiped tracked files. The guard must scan ancestors, not just the immediate directory.
    (tmp_path / ".git").mkdir()  # repo root
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    (sub / "tracked.py").write_text("committed = True", encoding="utf-8")
    guard = WorkspaceGuard(sub)  # workspace is a subdir, not the repo root
    snap = guard.snapshot()

    (sub / "appeared_after.py").write_text("x = 1", encoding="utf-8")

    guard.restore(snap)
    assert (sub / "appeared_after.py").exists()  # delete pass skipped — inside a repo
    assert (sub / "tracked.py").exists()


def test_non_repo_workspace_still_prunes_new_files(tmp_path: Path) -> None:
    # The guard above must NOT weaken the normal case: without a `.git` root, the delete-new pass still
    # cleans up files the agent created (that is verify-or-revert doing its job in a scratch workspace).
    (tmp_path / "keep.py").write_text("k = 1", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()

    (tmp_path / "created_by_agent.py").write_text("junk = 1", encoding="utf-8")

    guard.restore(snap)
    assert not (tmp_path / "created_by_agent.py").exists()  # pruned, as before
    assert (tmp_path / "keep.py").exists()


def test_restore_recreates_deleted_file(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("content", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()

    (tmp_path / "keep.txt").unlink()
    guard.restore(snap)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "content"


def test_restore_noop_when_unchanged(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()
    assert guard.restore(snap) == 0


def test_ignored_dirs_are_left_alone(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    snap = guard.snapshot()
    # a file created in an ignored dir is not deleted by restore
    (tmp_path / ".git" / "extra").write_text("y", encoding="utf-8")
    guard.restore(snap)
    assert (tmp_path / ".git" / "extra").exists()


def test_command_verifier_pass_and_fail(tmp_path: Path) -> None:
    assert CommandVerifier("exit 0", tmp_path).verify().passed is True
    failed = CommandVerifier("exit 1", tmp_path).verify()
    assert failed.passed is False


def test_null_verifier_passes() -> None:
    assert NullVerifier().verify().passed is True
