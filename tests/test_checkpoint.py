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
