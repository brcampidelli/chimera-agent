"""A folder says which repository, and which branch of it, it is a checkout of — read from `.git`.

Study 03 measured the owner's sidebar: 26 folders, 22 of them checkouts of ONE repository, each
listed as its own top-level project because the list grouped by the folder string. Git already
declares that a linked worktree belongs to its repository; `checkout_of` reads that declaration
without running git. These build real repositories and worktrees with `git` and check the reading
against what git itself made, including the cases that would quietly merge or split projects.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chimera.core.checkout import Checkout, checkout_of

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary")


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


def make_repo(path: Path, branch: str = "trunk") -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    # Set rather than `init -b`: the flag is newer than some gits this suite runs under.
    git(path, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


def real(path: Path) -> str:
    return str(path.resolve())


def rewrite(dotgit: Path, data: bytes) -> None:
    """Replace a `.git` file's content in place. Git marks it hidden on Windows, and opening a
    hidden file for a fresh write (`write_text`) is refused there; updating it is not."""
    with dotgit.open("r+b") as handle:
        handle.write(data)
        handle.truncate()


def test_the_main_checkout_is_its_own_project(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    found = checkout_of(repo)
    assert found == Checkout(
        project_root=real(repo), common_dir=real(repo / ".git"), branch="trunk", kind="main"
    )
    assert found.name == "repo"


def test_a_linked_worktree_belongs_to_the_repository_that_made_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "elsewhere" / "w-task"
    git(repo, "worktree", "add", "-q", "-b", "feat/x", str(wt))

    found = checkout_of(wt)
    assert found is not None
    assert found.kind == "worktree"
    assert found.project_root == real(repo)  # the repository, not the worktree's own folder
    assert found.common_dir == real(repo / ".git")
    assert found.branch == "feat/x"
    # And the main checkout still answers for itself, with the same project key.
    main = checkout_of(repo)
    assert main is not None and main.project_root == found.project_root


def test_a_subfolder_answers_for_the_checkout_that_contains_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "w-sub"
    git(repo, "worktree", "add", "-q", "-b", "sub", str(wt))

    in_main = checkout_of(repo / "src")
    in_wt = checkout_of(wt / "src")
    assert in_main is not None and in_main.kind == "main" and in_main.project_root == real(repo)
    assert in_wt is not None and in_wt.kind == "worktree" and in_wt.project_root == real(repo)
    assert in_wt.branch == "sub"


def test_a_detached_head_has_no_branch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "w-detached"
    git(repo, "worktree", "add", "-q", "--detach", str(wt))
    found = checkout_of(wt)
    assert found is not None and found.kind == "worktree" and found.branch is None

    git(repo, "checkout", "-q", "--detach")
    main = checkout_of(repo)
    assert main is not None and main.kind == "main" and main.branch is None


def test_a_folder_outside_git_is_a_plain_project_of_its_own(tmp_path: Path) -> None:
    folder = tmp_path / "notes"
    (folder / "deep").mkdir(parents=True)
    around = checkout_of(tmp_path)
    if around is not None and around.kind != "plain":
        pytest.skip("the temporary folder is itself inside a git repository on this machine")
    assert checkout_of(folder) == Checkout(
        project_root=real(folder), common_dir=None, branch=None, kind="plain"
    )
    # A subfolder of a plain folder is its own plain project: there is no repository to walk up to.
    deep = checkout_of(folder / "deep")
    assert deep is not None and deep.kind == "plain" and deep.project_root == real(folder / "deep")


def test_a_missing_folder_is_unknown_not_plain(tmp_path: Path) -> None:
    """None, so a caller keeps what it learned before; `plain` would overwrite a good stamp."""
    assert checkout_of(tmp_path / "gone") is None
    afile = tmp_path / "file.txt"
    afile.write_text("x", encoding="utf-8")
    assert checkout_of(afile) is None


def test_two_clones_of_one_remote_are_two_projects(tmp_path: Path) -> None:
    """Never merged by remote URL: separate clones hold separate files and branches."""
    origin = make_repo(tmp_path / "origin")
    git(tmp_path, "clone", "-q", str(origin), "one")
    git(tmp_path, "clone", "-q", str(origin), "two")
    one = checkout_of(tmp_path / "one")
    two = checkout_of(tmp_path / "two")
    assert one is not None and two is not None
    assert one.kind == two.kind == "main"
    assert one.project_root != two.project_root
    assert one.common_dir != two.common_dir


def test_a_relative_gitdir_is_read_relative_to_the_folder(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "w-rel"
    git(repo, "worktree", "add", "-q", "-b", "rel", str(wt))
    admin = Path(git(wt, "rev-parse", "--absolute-git-dir"))
    rewrite(wt / ".git", f"gitdir: {os.path.relpath(admin, wt)}\n".encode())
    # `commondir` inside the admin dir is relative already (git writes "../.."); keep it so.
    assert not Path((admin / "commondir").read_text(encoding="utf-8").strip()).is_absolute()

    found = checkout_of(wt)
    assert found is not None and found.kind == "worktree"
    assert found.project_root == real(repo) and found.branch == "rel"
    # Git agrees the rewritten link still works — the test did not build a link git rejects.
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "rel"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spelling")
def test_a_windows_gitdir_with_backslashes_and_crlf_is_read(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "w-win"
    git(repo, "worktree", "add", "-q", "-b", "win", str(wt))
    admin = Path(git(wt, "rev-parse", "--absolute-git-dir"))
    backslashed = str(admin).replace("/", "\\")
    rewrite(wt / ".git", f"gitdir: {backslashed}\r\n".encode())
    found = checkout_of(wt)
    assert found is not None and found.kind == "worktree" and found.project_root == real(repo)
    assert found.branch == "win"


def test_a_worktree_whose_admin_dir_was_pruned_is_unknown(tmp_path: Path) -> None:
    """The folder exists but git no longer declares it part of anything: unknown, not `plain`."""
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "w-pruned"
    git(repo, "worktree", "add", "-q", "-b", "pruned", str(wt))
    shutil.rmtree(Path(git(wt, "rev-parse", "--absolute-git-dir")))
    assert checkout_of(wt) is None


def test_a_stamp_that_does_not_parse_reads_as_none() -> None:
    assert Checkout.from_dict(None) is None
    assert Checkout.from_dict({"project_root": "/r", "kind": "submarine"}) is None
    assert Checkout.from_dict({"project_root": "", "kind": "main"}) is None
    assert Checkout.from_dict(["main"]) is None
    back = Checkout.from_dict(
        {"project_root": "/r", "common_dir": "", "branch": 3, "kind": "worktree", "extra": 1}
    )
    assert back == Checkout(project_root="/r", common_dir=None, branch=None, kind="worktree")
    one = Checkout(project_root="/r", common_dir="/r/.git", branch="b", kind="main")
    assert Checkout.from_dict(one.to_dict()) == one
