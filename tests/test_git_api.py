"""Tests for the structured git helpers behind the Code screen's git panel (real git, no network)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.api.git_api import git_commit, git_diff, git_revert_paths, git_status

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _init_repo(path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


def _head(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()


# --- status -------------------------------------------------------------------------------------


def test_status_shows_a_modified_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    status = git_status(tmp_path)
    assert status["is_repo"] is True
    assert status["branch"]  # a real branch name (main/master), non-empty
    readme = next(f for f in status["files"] if f["path"] == "README.md")
    assert readme["y"] == "M"  # worktree-modified, not staged
    assert readme["staged"] is False and readme["untracked"] is False


def test_status_flags_an_untracked_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("x\n", encoding="utf-8")
    status = git_status(tmp_path)
    new = next(f for f in status["files"] if f["path"] == "new.txt")
    assert new["untracked"] is True and new["staged"] is False


def test_status_marks_a_staged_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "new.txt"], cwd=tmp_path, capture_output=True, check=True)
    new = next(f for f in git_status(tmp_path)["files"] if f["path"] == "new.txt")
    assert new["staged"] is True and new["untracked"] is False


# --- diff ---------------------------------------------------------------------------------------


def test_diff_returns_real_hunks(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("hi\nmore\n", encoding="utf-8")
    diff = git_diff(tmp_path)
    assert diff["is_repo"] is True
    assert "@@" in diff["patch"] and "+more" in diff["patch"]


def test_diff_scoped_to_a_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("hi\nmore\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "other.txt"], cwd=tmp_path, capture_output=True, check=True)
    diff = git_diff(tmp_path, path="README.md")
    assert "README.md" in diff["patch"] and "other.txt" not in diff["patch"]


# --- commit -------------------------------------------------------------------------------------


def test_commit_with_explicit_paths_moves_head(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    before = _head(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    out = git_commit(tmp_path, "add a only", ["a.txt"])  # explicit single path
    assert out["ok"] is True and out["commit"] and out["error"] is None
    assert _head(tmp_path) != before  # HEAD moved
    # b.txt was NOT staged (explicit-path staging, never add -A): it's still untracked.
    assert any(f["path"] == "b.txt" and f["untracked"] for f in git_status(tmp_path)["files"])


def test_commit_requires_a_message_and_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    assert git_commit(tmp_path, "  ", ["a.txt"])["ok"] is False  # empty message
    assert git_commit(tmp_path, "msg", [])["ok"] is False  # no paths


# --- revert (scoped discard) --------------------------------------------------------------------


def test_revert_reverts_a_modification_and_removes_untracked(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")  # tracked modification
    (tmp_path / "created.txt").write_text("new\n", encoding="utf-8")  # untracked new file
    out = git_revert_paths(tmp_path, ["README.md", "created.txt"])
    assert out["ok"] is True and out["error"] is None
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "hi\n"  # modification reverted
    assert not (tmp_path / "created.txt").exists()  # untracked-in-paths removed


def test_revert_is_scoped_and_leaves_other_files_alone(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("keep\n", encoding="utf-8")  # untracked, NOT in the revert set
    git_revert_paths(tmp_path, ["README.md"])
    assert (tmp_path / "keep.txt").exists()  # scoped revert didn't touch an out-of-scope file


# --- honest empty-state on a non-repo -----------------------------------------------------------


def test_status_and_diff_on_a_non_repo_return_is_repo_false(tmp_path: Path) -> None:
    assert git_status(tmp_path) == {"is_repo": False, "branch": "", "files": []}
    assert git_diff(tmp_path) == {"is_repo": False, "patch": ""}


def test_commit_and_revert_on_a_non_repo_are_honest_failures(tmp_path: Path) -> None:
    commit = git_commit(tmp_path, "msg", ["a.txt"])
    assert commit["ok"] is False and commit["error"] == "not a git repo"
    revert = git_revert_paths(tmp_path, ["a.txt"])
    assert revert["ok"] is False and revert["error"] == "not a git repo"
