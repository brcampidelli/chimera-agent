"""Tests for git-worktree isolation (real git, no network)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from chimera.core.worktree import GitWorktree, is_git_repo, run_in_worktree


def _init_repo(path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (path / "README.md").write_text("hi", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


def test_is_git_repo(tmp_path: Path) -> None:
    assert is_git_repo(tmp_path) is False
    _init_repo(tmp_path)
    assert is_git_repo(tmp_path) is True


def test_create_checks_out_head_then_removes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = GitWorktree.create(tmp_path)
    try:
        assert worktree.path.exists() and worktree.path != tmp_path
        assert (worktree.path / "README.md").exists()  # a checkout of HEAD
    finally:
        worktree.remove()
    assert not worktree.path.exists()


def test_copy_back_applies_only_changed_files(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = GitWorktree.create(tmp_path)
    try:
        (worktree.path / "new.py").write_text("x = 1", encoding="utf-8")
        (worktree.path / "README.md").write_text("changed", encoding="utf-8")
        assert worktree.copy_back_to(tmp_path) >= 2
        assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x = 1"
        assert (tmp_path / "README.md").read_text(encoding="utf-8") == "changed"
    finally:
        worktree.remove()


def test_run_in_worktree_copies_back_on_success(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    def run(ws: Path) -> bool:
        (ws / "out.txt").write_text("done", encoding="utf-8")
        return True

    assert run_in_worktree(tmp_path, run, succeeded=lambda r: r) is True
    assert (tmp_path / "out.txt").exists()  # verified work copied back


def test_run_in_worktree_discards_on_failure(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    def run(ws: Path) -> bool:
        (ws / "out.txt").write_text("done", encoding="utf-8")
        return False

    run_in_worktree(tmp_path, run, succeeded=lambda r: r)
    assert not (tmp_path / "out.txt").exists()  # isolated changes discarded


def test_run_in_worktree_without_git_runs_directly(tmp_path: Path) -> None:
    seen: dict[str, Path] = {}

    def run(ws: Path) -> bool:
        seen["ws"] = ws
        (ws / "out.txt").write_text("x", encoding="utf-8")
        return True

    run_in_worktree(tmp_path, run, succeeded=lambda r: r)
    assert seen["ws"] == tmp_path.resolve()  # no isolation outside a repo
    assert (tmp_path / "out.txt").exists()
