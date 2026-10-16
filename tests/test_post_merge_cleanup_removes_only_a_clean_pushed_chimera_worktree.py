"""The post-merge cleanup removes a worktree and its LOCAL branch only when nothing would be lost.

`chimera/core/pull_request_cleanup.py`, run against real git repositories: a bare "origin", a main
checkout, and worktrees in a folder standing in for Chimera's worktree root. Each refusal case keeps
the folder and the branch byte-for-byte, and a spy on the runner holds the other half of the rule —
across every case, no argv this module builds ever pushes, deletes a remote branch, or forces.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from chimera.core import pull_request_cleanup as cleanup
from chimera.core.pull_request import run_quiet


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A main checkout with a pushed ``main``, and the folder worktrees go in."""
    for name, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }.items():
        monkeypatch.setenv(name, value)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "main")
    (main / "a.txt").write_text("a\n", encoding="utf-8")
    _git(main, "add", "a.txt")
    _git(main, "commit", "-m", "first")
    _git(main, "remote", "add", "origin", str(origin))
    _git(main, "push", "-u", "origin", "main")
    root = tmp_path / "wt-root"
    root.mkdir()
    return main, root


def _worktree(main: Path, root: Path, name: str, *, push: bool = True) -> Path:
    path = root / f"chimera-wt-{name}"
    branch = f"chimera/{name}"
    _git(main, "worktree", "add", "-b", branch, str(path), "HEAD")
    (path / f"{name}.txt").write_text("work\n", encoding="utf-8")
    _git(path, "add", f"{name}.txt")
    _git(path, "commit", "-m", name)
    if push:
        _git(path, "push", "-u", "origin", branch)
    return path


def _branches(main: Path) -> set[str]:
    return set(_git(main, "branch", "--format=%(refname:short)").split())


class Spy:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return run_quiet(argv, cwd)


@pytest.fixture
def spy() -> object:
    found = Spy()
    yield found
    for argv in found.calls:
        assert "push" not in argv, argv
        assert "--force" not in argv and "-f" not in argv and "-D" not in argv, argv
        assert "--delete" not in argv and not any(a.startswith(":") for a in argv), argv
        assert "origin" not in argv, argv  # nothing here names the remote, so nothing reaches it


def test_a_clean_pushed_worktree_and_its_local_branch_are_removed_but_not_the_remote(
    repo: tuple[Path, Path], spy: Spy
) -> None:
    main, root = repo
    path = _worktree(main, root, "done")

    result = cleanup.remove_merged_worktree(path, roots=[root], runner=spy)

    assert result.removed and result.kept == "" and result.branch == "chimera/done"
    assert not path.exists()
    assert "chimera/done" not in _branches(main)
    assert "chimera/done" in _git(main, "ls-remote", "--heads", "origin")  # the remote is untouched


def test_uncommitted_work_keeps_everything(repo: tuple[Path, Path], spy: Spy) -> None:
    main, root = repo
    path = _worktree(main, root, "dirty")
    (path / "new.txt").write_text("not committed\n", encoding="utf-8")  # untracked counts

    result = cleanup.remove_merged_worktree(path, roots=[root], runner=spy)

    assert not result.removed and result.kept == cleanup.DIRTY
    assert (path / "new.txt").is_file() and "chimera/dirty" in _branches(main)


def test_a_commit_not_pushed_keeps_everything(repo: tuple[Path, Path], spy: Spy) -> None:
    main, root = repo
    path = _worktree(main, root, "ahead")
    (path / "more.txt").write_text("more\n", encoding="utf-8")
    _git(path, "add", "more.txt")
    _git(path, "commit", "-m", "not pushed")

    result = cleanup.remove_merged_worktree(path, roots=[root], runner=spy)

    assert not result.removed and result.kept == cleanup.UNPUSHED
    assert path.is_dir() and "chimera/ahead" in _branches(main)


def test_a_branch_without_upstream_counts_as_not_pushed(repo: tuple[Path, Path], spy: Spy) -> None:
    main, root = repo
    path = _worktree(main, root, "local", push=False)

    result = cleanup.remove_merged_worktree(path, roots=[root], runner=spy)

    assert not result.removed and result.kept == cleanup.NO_UPSTREAM
    assert path.is_dir() and "chimera/local" in _branches(main)


def test_a_worktree_outside_the_root_is_never_a_candidate(
    repo: tuple[Path, Path], tmp_path: Path, spy: Spy
) -> None:
    main, root = repo
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    path = _worktree(main, elsewhere, "outside")

    result = cleanup.remove_merged_worktree(path, roots=[root], runner=spy)

    assert not result.removed and result.kept == cleanup.NOT_CHIMERA_WORKTREE
    assert path.is_dir() and "chimera/outside" in _branches(main)
    assert spy.calls == []  # refused before git was even asked


def test_a_hand_named_folder_inside_the_root_is_never_a_candidate(
    repo: tuple[Path, Path], spy: Spy
) -> None:
    main, root = repo
    path = root / "my-own-checkout"
    _git(main, "worktree", "add", "-b", "mine", str(path), "HEAD")

    result = cleanup.remove_merged_worktree(path, roots=[root], runner=spy)

    assert not result.removed and result.kept == cleanup.NOT_CHIMERA_WORKTREE
    assert path.is_dir()


def test_the_main_checkout_is_never_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even named like one of ours and lying in the root: the main checkout is refused."""
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "t")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "t@example.com")
    main = tmp_path / "chimera-wt-main"
    main.mkdir()
    _git(main, "init", "-b", "main")
    (main / "a.txt").write_text("a\n", encoding="utf-8")
    _git(main, "add", "a.txt")
    _git(main, "commit", "-m", "first")
    spy = Spy()

    result = cleanup.remove_merged_worktree(main, roots=[tmp_path], runner=spy)

    assert not result.removed and result.kept == cleanup.MAIN_CHECKOUT
    assert (main / "a.txt").is_file()
    assert not any("remove" in argv or "branch" in argv for argv in spy.calls)


def test_the_branch_the_main_checkout_has_out_is_never_deleted(tmp_path: Path) -> None:
    """Git will not normally let two checkouts share a branch, so the state is staged with a fake
    runner: the worktree reports the branch the main checkout has out. Refused, and the refusal
    comes before any command that would remove or delete."""
    root = tmp_path / "root"
    path = root / "chimera-wt-x"
    path.mkdir(parents=True)
    main = tmp_path / "main"
    calls: list[list[str]] = []

    def fake(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        args = list(argv)[3:]  # past "git -c core.fsmonitor=false"
        answers = {
            "rev-parse": f"{main / '.git'}\n",
            "worktree": f"worktree {main}\nworktree {path}\n",
            "symbolic-ref": "feature\n",
        }
        return subprocess.CompletedProcess(argv, 0, answers.get(args[0], ""), "")

    result = cleanup.remove_merged_worktree(path, roots=[root], runner=fake)

    assert not result.removed and result.kept == cleanup.CURRENT_BRANCH
    assert not any("remove" in argv or "-d" in argv for argv in calls)
    assert path.is_dir()
