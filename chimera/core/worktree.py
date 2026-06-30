"""Git-worktree isolation for autonomous attempts (HORIZON-style).

When the workspace is a git repository, a run can execute in an isolated *worktree* —
a separate checkout on a throwaway branch — so the agent's edits never touch the main
checkout until they are verified. On success only the files the agent actually changed
are copied back (so a user's other uncommitted work is preserved); either way the
worktree is removed. Outside a git repo this is a no-op (the run uses the workspace
directly), so callers can always opt in safely.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from chimera.core.checkpoint import _IGNORE_DIRS
from chimera.telemetry import get_logger

_log = get_logger("core.worktree")

T = TypeVar("T")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )


def is_git_repo(path: Path) -> bool:
    try:
        result = _git(["rev-parse", "--is-inside-work-tree"], Path(path))
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


class GitWorktree:
    """A throwaway git worktree on its own branch, created from the repo's HEAD."""

    def __init__(self, path: Path, branch: str, repo_root: Path) -> None:
        self.path = path
        self.branch = branch
        self.repo_root = repo_root

    @classmethod
    def create(cls, repo_root: Path, *, prefix: str = "chimera") -> GitWorktree:
        repo_root = Path(repo_root).resolve()
        branch = f"{prefix}/attempt-{uuid.uuid4().hex[:8]}"
        path = Path(tempfile.mkdtemp(prefix="chimera-wt-"))
        path.rmdir()  # `git worktree add` needs the target not to exist yet
        result = _git(["worktree", "add", "-b", branch, str(path), "HEAD"], repo_root)
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")
        _log.debug("created worktree %s on %s", path, branch)
        return cls(path, branch, repo_root)

    def changed_paths(self) -> list[str]:
        """Paths the agent added/modified/deleted in the worktree, relative to root."""
        _git(["add", "-A"], self.path)  # stage so untracked files show as changes
        result = _git(["diff", "--cached", "--name-only", "HEAD"], self.path)
        return [line for line in result.stdout.splitlines() if line.strip()]

    def copy_back_to(self, dest: Path) -> int:
        """Apply only the changed files to ``dest``. Returns the number of changes."""
        dest = Path(dest).resolve()
        count = 0
        for rel in self.changed_paths():
            if any(part in _IGNORE_DIRS for part in Path(rel).parts):
                continue
            src = self.path / rel
            target = dest / rel
            if src.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            else:
                target.unlink(missing_ok=True)  # the agent deleted it
            count += 1
        return count

    def remove(self) -> None:
        _git(["worktree", "remove", "--force", str(self.path)], self.repo_root)
        _git(["branch", "-D", self.branch], self.repo_root)
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)


def run_in_worktree(
    workspace: Path,
    run: Callable[[Path], T],
    *,
    succeeded: Callable[[T], bool],
) -> T:
    """Run ``run`` against an isolated git worktree of ``workspace``.

    Outside a git repo, runs against ``workspace`` directly (no isolation). Inside one,
    edits land in a throwaway worktree and are copied back only when ``succeeded``.
    """
    workspace = Path(workspace).resolve()
    if not is_git_repo(workspace):
        return run(workspace)

    worktree = GitWorktree.create(workspace)
    try:
        result = run(worktree.path)
        if succeeded(result):
            changed = worktree.copy_back_to(workspace)
            _log.debug("worktree succeeded; copied %d changed file(s) back", changed)
        else:
            _log.debug("worktree failed; discarding the isolated changes")
        return result
    finally:
        worktree.remove()
