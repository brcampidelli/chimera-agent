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
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TypeVar

from chimera.core.checkpoint import _IGNORE_DIRS
from chimera.telemetry import get_logger

_log = get_logger("core.worktree")

T = TypeVar("T")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one git command and read its output as UTF-8.

    The encoding is named, and that is the whole point of this function existing. ``text=True``
    alone decodes with the machine's locale — cp1252 on a default Windows install, where the bytes
    ``0x81 0x8D 0x8F 0x90 0x9D`` are undefined. Those bytes are ordinary inside UTF-8: they appear
    in emoji, in most CJK, and in plenty of prose.

    The failure was worse than an error. ``UnicodeDecodeError`` is raised on the reader thread, so
    the call returns a *successful* process whose ``stdout`` is ``None`` — and ``GET /api/git/diff``
    fed that ``None`` into a response field typed ``str`` and answered **500, in plain text**, so a
    client calling ``.json()`` on the body broke a second time. Reproducible against Chimera's own
    installation directory, whose JavaScript bundles contain those bytes; ``git/status`` survived
    the same repository only because status prints file names and diff prints content.

    ``errors="replace"`` rather than strict: a diff viewer that refuses to show a file because one
    byte in it is not valid UTF-8 helps nobody. Git writes UTF-8 on every platform, so this is the
    right codec rather than a guess — and every git call in the app goes through here, so naming it
    once is also the only way to be sure it is named at all.
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def is_git_repo(path: Path) -> bool:
    try:
        result = _git(["rev-parse", "--is-inside-work-tree"], Path(path))
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


#: A leftover temp directory younger than this is assumed to belong to a run that is still starting.
#: `GitWorktree.create` makes the directory and registers it with git a moment later, and pruning
#: inside that window would delete a live worktree out from under a concurrent process.
_ORPHAN_MIN_AGE_SECONDS = 3600


def live_worktree_paths(repo_root: Path) -> set[Path]:
    """Paths git currently knows as worktrees of this repo."""
    result = _git(["worktree", "list", "--porcelain"], repo_root)
    paths: set[Path] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            with suppress(OSError, ValueError):
                paths.add(Path(line[len("worktree ") :].strip()).resolve())
    return paths


def prune_orphans(repo_root: Path, *, prefix: str = "chimera") -> dict[str, int]:
    """Clean up what a killed run leaves behind. Returns what was removed, by kind.

    `GitWorktree.remove` runs in a `finally`, so the ordinary paths — success, failure, an
    exception — all clean up. SIGKILL does not run `finally`, and neither does a power cut or a
    container stop, and there was nothing anywhere that collected the remains:

      * an entry under `.git/worktrees`,
      * a branch `chimera/attempt-<hex>`,
      * a temp directory.

    All three land in the USER's repository. `git worktree list` and `git branch` are things people
    read, so this is litter we leave in someone's house — and `git worktree prune` was not called
    from anywhere in the codebase.

    DELIBERATELY NOT a registry of our own, which is what the proposal asked for. Git already keeps
    a durable one in `.git/worktrees`, and a second record can disagree with it — at which point the
    cleanup is deciding between two sources of truth about whether a directory is live. What git
    does not track is the branch and the temp directory, and those are handled from git's own state
    rather than from a file we would have to keep correct across crashes.

    HONEST STARTING POINT: this repository has zero `chimera/*` branches right now. The item is
    justified by the shape of the failure, not by observed leakage.
    """
    removed = {"worktrees": 0, "branches": 0, "directories": 0}
    if not is_git_repo(repo_root):
        return removed
    repo_root = Path(repo_root).resolve()

    # 1. Git's own pruning: drops `.git/worktrees` entries whose directory is gone. Safe by
    #    construction — it only forgets what is already missing.
    before = live_worktree_paths(repo_root)
    _git(["worktree", "prune"], repo_root)
    live = live_worktree_paths(repo_root)
    removed["worktrees"] = max(0, len(before) - len(live))

    # 2. Branches with no worktree attached. `git branch -D` refuses a branch checked out in a live
    #    worktree, so a run in flight cannot be harmed even if the listing raced.
    result = _git(["branch", "--list", f"{prefix}/attempt-*", "--format=%(refname:short)"], repo_root)
    for branch in (b.strip() for b in result.stdout.splitlines() if b.strip()):
        if _git(["branch", "-D", branch], repo_root).returncode == 0:
            removed["branches"] += 1

    # 3. Temp directories git no longer knows about. Age-gated: `create` makes the directory and
    #    registers it a moment later, and pruning inside that window would delete a live worktree
    #    belonging to another process.
    now = time.time()
    with suppress(OSError):
        for candidate in Path(tempfile.gettempdir()).glob("chimera-wt-*"):
            if not candidate.is_dir() or candidate.resolve() in live:
                continue
            with suppress(OSError):
                if now - candidate.stat().st_mtime < _ORPHAN_MIN_AGE_SECONDS:
                    continue
                shutil.rmtree(candidate, ignore_errors=True)
                removed["directories"] += 1

    if any(removed.values()):
        _log.info("pruned orphaned worktrees: %s", removed)
    return removed


#: Pruned once per process, before the first worktree is created — "on boot" in the only sense that
#: matters, and free for the runs that never use one.
_pruned_repos: set[Path] = set()


class GitWorktree:
    """A throwaway git worktree on its own branch, created from the repo's HEAD."""

    def __init__(self, path: Path, branch: str, repo_root: Path) -> None:
        self.path = path
        self.branch = branch
        self.repo_root = repo_root

    @classmethod
    def create(cls, repo_root: Path, *, prefix: str = "chimera") -> GitWorktree:
        repo_root = Path(repo_root).resolve()
        if repo_root not in _pruned_repos:
            _pruned_repos.add(repo_root)
            with suppress(Exception):
                # Best-effort and never fatal: failing to tidy up after a previous crash is not a
                # reason to refuse to start this run.
                prune_orphans(repo_root, prefix=prefix)
        branch = f"{prefix}/attempt-{uuid.uuid4().hex[:8]}"
        path = Path(tempfile.mkdtemp(prefix="chimera-wt-"))
        path.rmdir()  # `git worktree add` needs the target not to exist yet
        result = _git(["worktree", "add", "-b", branch, str(path), "HEAD"], repo_root)
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")
        _log.debug("created worktree %s on %s", path, branch)
        return cls(path, branch, repo_root)

    def changed_paths(self) -> list[str]:
        """Paths the agent added/modified/deleted in the worktree, relative to root.

        Filtered by ``_IGNORE_DIRS``, and not only to be tidy. `copy_back_to` already refuses to
        copy these, but the CONFLICT set is computed from this list — so a `__pycache__` written
        by running the verify command in two worktrees became two workers "both changing the same
        file", and the bytecode was reported to a person as a contested file alongside their real
        one. Ignored here means ignored everywhere it is counted.
        """
        _git(["add", "-A"], self.path)  # stage so untracked files show as changes
        result = _git(["diff", "--cached", "--name-only", "HEAD"], self.path)
        return [
            line
            for line in result.stdout.splitlines()
            if line.strip() and not any(part in _IGNORE_DIRS for part in Path(line).parts)
        ]

    def copy_back_to(self, dest: Path, *, only: set[str] | None = None) -> int:
        """Apply the changed files to ``dest``. Returns the number of changes.

        When ``only`` is given, restrict the copy to those relative paths (used to skip
        files another isolated worker also touched — i.e. cross-worker conflicts).
        """
        dest = Path(dest).resolve()
        count = 0
        for rel in self.changed_paths():
            if only is not None and rel not in only:
                continue
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
