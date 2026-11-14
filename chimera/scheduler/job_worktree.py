"""Running a scheduled job in a worktree of its folder, and deciding afterwards whether to keep it.

A job that edits files on a schedule edits them in whatever state the folder happens to be in at 3
a.m.: on top of the branch the owner was in the middle of, next to changes they had not saved to a
commit. A worktree gives the run its own checkout on its own branch, so the owner's folder is never
touched, and the question left is what to do with the checkout when the run ends.

**Nobody is looking when it is removed, so it is removed by the rules the unattended cleanup already
has** (`worktree_expiry`, `worktree_prune.prune_worktrees(protect_ignored=True)`), not by a second
set written here: never ``--force``, never ``git branch -D``, and kept for ANY of a dirty tree, a
commit that is on no branch the repository knows, or an ignored file (a ``.env``, a report, a local
database: none of those is in ``git status`` and all of them are lost with the folder). The one
thing cleared first is the obvious regenerable junk (``__pycache__``, ``*.pyc`` and the test and lint
caches), because a report job that ran Python is not "work". Everything else, a ``dist/`` or a
``build/`` included, counts. In doubt it is kept, and the history says which refusal kept it.

The worktree is the *durable* kind (the one a Code conversation gets): a kept one has to survive the
process and the temp-folder cleanup, and the app's worktree cleanup then lists it for the owner to
remove by hand. Never removed by this module once kept.

When the job's folder is a subfolder of the repository, the run works in the same subfolder of the
worktree, not at its root: the job's reach must not be wider than the folder the owner named.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from pathlib import Path

from chimera.core.pull_request import run_quiet
from chimera.core.pull_request_cleanup import Runner
from chimera.core.worktree import GitWorktree, _app_registry_path, _git, is_git_repo
from chimera.core.worktree_prune import prune_worktrees
from chimera.scheduler.history import CronHistory, HistoryRow
from chimera.scheduler.models import CronJob, JobOutcome
from chimera.telemetry import get_logger

_log = get_logger("scheduler.job_worktree")

#: Branch prefix of a job's worktree, so ``git branch`` says whose it is.
BRANCH_PREFIX = "chimera-cron"

#: Directories that hold only what running the code regenerates. Deliberately short: anything not
#: named here keeps the worktree. A ``.pyc`` is junk only INSIDE one of these (``__pycache__`` is the
#: Python one): a ``dist/app.pyc`` is somebody's output, and a repository may version a ``.pyc`` of
#: its own.
_JUNK_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def _purge(folder: Path, root: Path, tracked: frozenset[str]) -> None:
    """Delete the files under a cache folder that git does not track, then the emptied folders."""
    for here, _dirs, files in os.walk(folder):
        for name in files:
            path = Path(here) / name
            if path.relative_to(root).as_posix() not in tracked:
                path.unlink(missing_ok=True)
    for here, _dirs, _files in os.walk(folder, topdown=False):
        try:
            Path(here).rmdir()  # only succeeds when empty: a tracked file keeps its folder
        except OSError:
            continue


def _clear_junk(root: Path, runner: Runner = run_quiet) -> None:
    """Delete the regenerable caches under ``root`` (never inside ``.git``, never a tracked file),
    so they do not read as work. Does nothing when git cannot say what is tracked: in doubt, keep."""
    listing = runner(["git", "ls-files", "-z"], root)
    if listing.returncode != 0:
        return
    tracked = frozenset(e for e in (listing.stdout or "").split("\x00") if e)
    for here, dirs, _files in os.walk(root):
        if Path(here) == root and ".git" in dirs:
            dirs.remove(".git")
        for name in [d for d in dirs if d in _JUNK_DIRS]:
            _purge(Path(here) / name, root, tracked)
            dirs.remove(name)


def _note(history: CronHistory, job: CronJob, worktree: GitWorktree, detail: str) -> None:
    _log.info("cron job %s (%s) keeps %s: %s", job.name, job.id, worktree.path, detail)
    history.append(
        HistoryRow(
            at=time.time(), job_id=job.id, name=job.name, outcome="note",
            reason="worktree_kept", detail=detail[:300], worktree=str(worktree.path),
        )
    )


def _settle(
    job: CronJob, worktree: GitWorktree, history: CronHistory, runner: Runner = run_quiet
) -> None:
    """Remove the worktree when the unattended-removal rules allow it; otherwise keep it and say why.

    The refusal's code is the reason in the history (``dirty``, ``ignored_files``, ``no_upstream``
    for a commit nothing else has, ``git_failed``...). Anything unexpected keeps it.
    """
    try:
        _clear_junk(worktree.path, runner)
    except Exception:  # a cache that would not go is read as work, and keeps it
        _log.warning("cron job %s (%s): could not clear caches in %s", job.name, job.id, worktree.path)
    try:
        results = prune_worktrees(
            worktree.repo_root, [str(worktree.path)], dry_run=False, protect_ignored=True,
            runner=runner,
        )
    except Exception as exc:  # in doubt, keep
        _note(history, job, worktree, f"branch {worktree.branch}: could not check ({type(exc).__name__})")
        return
    result = results[0] if results else None
    if result is None or not result.removed:
        reason = result.kept if result is not None and result.kept else "unknown"
        _note(history, job, worktree, f"branch {worktree.branch}: kept ({reason})")
        return
    # The folder is gone. The branch has no commit of its own that another branch lacks (that is what
    # "removable" meant), so `-d` (never `-D`) takes it. Both steps below are tidying: a failure in
    # either leaves a stray branch or a stale record, never a lost outcome.
    try:
        runner(["git", "branch", "-d", worktree.branch], worktree.repo_root)
    except Exception:
        _log.warning("cron job %s (%s): could not delete branch %s", job.name, job.id, worktree.branch)
    try:
        from chimera.core.code_projects import AppWorktreeRegistry

        AppWorktreeRegistry(worktree.app_registry or _app_registry_path()).remove(worktree.path)
    except Exception:
        _log.warning("cron job %s (%s): could not update the worktree record", job.name, job.id)


def _settle_safely(
    job: CronJob, worktree: GitWorktree, history: CronHistory, runner: Runner
) -> None:
    """:func:`_settle`, whatever happens: the run's outcome, or the exception it raised, comes first."""
    try:
        _settle(job, worktree, history, runner)
    except Exception as exc:
        _log.warning("cron job %s (%s): worktree not settled (%s); it was kept", job.name, job.id,
                     type(exc).__name__)
        with contextlib.suppress(Exception):
            _note(history, job, worktree, f"kept: could not settle ({type(exc).__name__})")


def _toplevel(folder: Path) -> Path | None:
    done = _git(["rev-parse", "--show-toplevel"], folder)
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def run_in_job_worktree(
    job: CronJob,
    repo_root: Path,
    history: CronHistory,
    run: Callable[[Path], JobOutcome],
    *,
    runner: Runner = run_quiet,
) -> JobOutcome:
    """Run ``run`` against a fresh worktree of ``repo_root``'s repository and settle it afterwards.

    ``run`` receives the same folder inside the worktree that ``repo_root`` is inside the repository.

    Raises ``RuntimeError`` when the folder is not a git repository, or does not exist in the
    repository's history: the job asked to be kept apart from the folder, and running it in the
    folder anyway would be the one answer the owner did not ask for. The engine records that as an
    error, so the job's row says why.
    """
    repo_root = Path(repo_root)
    if not is_git_repo(repo_root):
        raise RuntimeError(
            f"this task asks to run in a worktree, but {repo_root} is not a git repository"
        )
    top = _toplevel(repo_root) or repo_root
    try:
        rel = repo_root.resolve().relative_to(top.resolve())
    except ValueError:
        rel = Path(".")
    worktree = GitWorktree.create(top, prefix=BRANCH_PREFIX, durable=True, label="cron")
    target = worktree.path / rel
    if not target.is_dir():
        _settle_safely(job, worktree, history, runner)
        raise RuntimeError(
            f"this task's folder {rel.as_posix()} is not tracked by git, so a worktree has no copy of it"
        )
    try:
        outcome = run(target)
    except BaseException:
        # The run raised (or was abandoned): whatever it wrote before that is still work.
        _settle_safely(job, worktree, history, runner)
        raise
    _settle_safely(job, worktree, history, runner)
    return outcome
