"""What a killed run leaves in someone else's repository.

`GitWorktree.remove` runs in a `finally`, so success, failure and exceptions all clean up. SIGKILL
does not run `finally` — and neither does a power cut, a `docker stop`, or the OOM killer. What
survives is an entry under `.git/worktrees`, a branch `chimera/attempt-<hex>`, and a temp directory,
and nothing in the codebase collected any of it: `git worktree prune` was called from nowhere.

That litter is in the USER's repository. `git worktree list` and `git branch` are things people
read, so a run that dies leaves a mess in a place its owner looks.

HONEST STARTING POINT, kept out of the commit message where it would rot: this repository has zero
`chimera/*` branches today. The item is justified by the construction of the failure, not by
leakage anyone observed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chimera.core.worktree import GitWorktree, live_worktree_paths, prune_orphans


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(["init", "-q"], root)
    git(["config", "user.email", "t@example.com"], root)
    git(["config", "user.name", "t"], root)
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    git(["add", "-A"], root)
    git(["commit", "-qm", "init"], root)
    return root


def branches(root: Path) -> list[str]:
    out = git(["branch", "--list", "chimera/attempt-*", "--format=%(refname:short)"], root).stdout
    return [b.strip() for b in out.splitlines() if b.strip()]


def test_a_run_that_never_cleaned_up_leaves_all_three_traces(repo: Path) -> None:
    """The state this exists to fix, asserted before fixing it — otherwise the fix proves nothing."""
    wt = GitWorktree.create(repo)
    assert wt.path.exists()
    assert branches(repo) == [wt.branch]
    assert wt.path.resolve() in live_worktree_paths(repo)


def test_pruning_collects_the_branch_a_killed_run_left(repo: Path) -> None:
    """The directory is gone (the OS reclaimed /tmp, or the user deleted it) but git still has both.

    This is the shape after a hard kill plus a reboot, and it is the one that pollutes what a person
    reads: `git branch` in their own repository, listing our attempts forever.
    """
    wt = GitWorktree.create(repo)
    import shutil

    shutil.rmtree(wt.path)  # the run died; nothing called remove()

    assert branches(repo) == [wt.branch], "precondition: the branch is still there"
    removed = prune_orphans(repo)

    assert branches(repo) == []
    assert removed["branches"] == 1
    assert removed["worktrees"] == 1


def test_pruning_does_not_touch_a_live_worktree(repo: Path) -> None:
    """The half that matters more than the cleanup.

    A prune that removes a worktree another process is working in destroys real work — worse than
    the litter it was collecting. `git branch -D` refuses a branch checked out in a live worktree,
    which is what makes this safe even if the listing raced.
    """
    wt = GitWorktree.create(repo)
    prune_orphans(repo)

    assert wt.path.exists(), "a live worktree was deleted"
    assert branches(repo) == [wt.branch], "a live branch was deleted"
    assert (wt.path / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_a_fresh_temp_directory_is_left_alone(repo: Path, tmp_path: Path) -> None:
    """`create` makes the directory and registers it with git a moment later.

    Pruning inside that window would delete a live worktree out from under a concurrent process, so
    the directory sweep is age-gated. Nothing here is old enough to collect.
    """
    import tempfile

    fresh = Path(tempfile.mkdtemp(prefix="chimera-wt-"))
    try:
        assert prune_orphans(repo)["directories"] == 0
        assert fresh.exists()
    finally:
        import shutil

        shutil.rmtree(fresh, ignore_errors=True)


def test_pruning_a_directory_that_is_not_a_repo_is_a_no_op(tmp_path: Path) -> None:
    assert prune_orphans(tmp_path) == {"worktrees": 0, "branches": 0, "directories": 0}


def test_the_first_create_in_a_process_prunes_first(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring, which is a separate claim from the pruning working.

    `prune_orphans` passing its own tests says nothing about anything ever calling it — and a
    cleanup nobody invokes is the same as no cleanup. Pruning happens once per repo per process,
    before the first worktree: "on boot" in the only sense that matters, and free for the runs that
    never make one.
    """
    import chimera.core.worktree as mod

    monkeypatch.setattr(mod, "_pruned_repos", set())
    calls: list[Path] = []
    real = mod.prune_orphans
    monkeypatch.setattr(mod, "prune_orphans", lambda root, **kw: (calls.append(root), real(root, **kw))[1])

    first = GitWorktree.create(repo)
    second = GitWorktree.create(repo)
    try:
        assert calls == [repo.resolve()], "pruned on the first create, and only once"
    finally:
        first.remove()
        second.remove()
