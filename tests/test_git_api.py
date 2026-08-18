"""Tests for the structured git helpers behind the Code screen's git panel (real git, no network)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.api.git_api import git_commit, git_diff, git_init, git_revert_paths, git_status

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


def mock_branch(path: Path) -> str:
    """Whatever `git init` named the default branch here (main or master, per the git version)."""
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()


def git_init_with_home(path: Path, home: Path) -> dict[str, object]:
    """Run `git_init` with git pointed at an EMPTY home, i.e. with no user identity configured.

    Pointing HOME/XDG at a temp dir is the only way to observe the fresh-laptop case from a machine
    that has a global .gitconfig — and every machine that runs this suite has one, which is why the
    fallback shipped untested the first time somebody wrote this feature by hand.
    """
    import os

    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home)})
    os.environ["GIT_CONFIG_GLOBAL"] = str(home / "gitconfig-that-does-not-exist")
    try:
        return git_init(path)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# --- init ---------------------------------------------------------------------------------------


def test_init_makes_a_repo_and_a_snapshot_to_come_back_to(tmp_path: Path) -> None:
    """`git init` alone leaves a repo with no HEAD — isolation with nothing to return to.

    The commit is what makes this worth a button: it is taken before the agent is handed write and
    shell access to the folder, which is the only moment at which it can be taken.
    """
    (tmp_path / "app.py").write_text("print('x')\n", encoding="utf-8")

    result = git_init(tmp_path)

    assert result["ok"] is True and result["error"] is None
    assert result["commit"]  # a real short hash, so there is something to return to
    assert git_status(tmp_path) == {"is_repo": True, "branch": mock_branch(tmp_path), "files": []}


def test_init_commits_even_when_git_does_not_know_who_you_are(tmp_path: Path) -> None:
    """The common case on a fresh laptop, and the one that made this a terminal errand.

    `git commit` refuses outright without a `user.email`. A button whose answer is "now go configure
    git" has the exact failure this feature exists to remove, so the snapshot falls back to an
    identity of its own.
    """
    (tmp_path / "app.py").write_text("x\n", encoding="utf-8")
    home = tmp_path / "empty-home"  # no ~/.gitconfig, so git has no identity to find
    home.mkdir()

    result = git_init_with_home(tmp_path, home)

    assert result["ok"] is True and result["commit"]


def test_init_refuses_a_folder_that_is_already_a_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    head = _head(tmp_path)

    result = git_init(tmp_path)

    assert result["ok"] is False and result["error"] == "already a git repo"
    assert _head(tmp_path) == head  # and it did not commit over the user's work


def test_init_refuses_a_subdirectory_of_an_existing_repo(tmp_path: Path) -> None:
    """A nested repo reads as a helpful button and behaves as a folder that left its project."""
    _init_repo(tmp_path)
    child = tmp_path / "vendor"
    child.mkdir()

    assert git_init(child)["ok"] is False
    assert not (child / ".git").exists()


def test_an_empty_folder_is_initialised_with_nothing_to_snapshot(tmp_path: Path) -> None:
    # `git commit` with an empty index exits non-zero, so "nothing to commit" has to be CHECKED —
    # inferring it from the exit code would report a working init as a failure.
    result = git_init(tmp_path)

    assert result["ok"] is True and result["commit"] == "" and result["error"] is None
    assert git_status(tmp_path)["is_repo"] is True


def test_init_on_a_machine_without_git_is_an_error_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one helper here that runs git after `is_git_repo` has already answered no.

    A machine with no git makes that gate return False — the same answer as "an ordinary folder" —
    so this helper proceeds and hits the missing binary itself. `FileNotFoundError` is an OSError and
    NOT a `subprocess.SubprocessError`, which is how the first version of this caught the timeout and
    let the missing binary through as a 500.
    """
    from chimera.api import git_api

    def no_git(args: list[str], cwd: Path) -> object:
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(git_api, "_git", no_git)
    monkeypatch.setattr(git_api, "is_git_repo", lambda _path: False)

    result = git_api.git_init(Path("."))

    assert result["ok"] is False
    assert result["error"] and "git failed" in result["error"]


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
