"""After a pull request merges: remove the conversation's worktree and LOCAL branch, or keep both.

The owner can switch this on per conversation (off by default). It is the only code in the PR cycle
that deletes anything, so it is written as a list of refusals with removal at the end, and every
refusal leaves the disk exactly as it was. Removal happens only when ALL of these hold:

* **The folder is one of Chimera's worktrees.** Its name starts with ``chimera-wt-`` and it lies in a
  folder worktrees are made in (`worktree.worktree_parents`, or its own repository's
  ``.chimera-worktrees/<name>`` folder beside it). A conversation about the owner's own
  checkout, or about a worktree they made by hand, is never a candidate — the app did not make it,
  so the app does not get to unmake it.
* **It is not the main checkout**, and git lists it as a worktree of that checkout.
* **Its branch is not the branch the main checkout has out.** ``git branch -d`` would refuse that one
  anyway; refusing it here names the reason instead of printing git's.
* **Nothing would be lost.** ``git status --porcelain`` is empty (untracked files count), the branch
  has an upstream, and ``git rev-list --count @{u}..HEAD`` is zero. A branch with NO upstream counts
  as unpushed: "I cannot tell whether this was sent" is not "this was sent".

What it never does: push, delete a remote branch, force anything. The worktree goes with
``git worktree remove`` (no ``--force``, so git itself refuses a dirty tree a second time) and the
branch with ``git branch -d`` (lowercase: git refuses a branch not merged into its upstream). Every
git call is an argument list through the same no-shell runner the pull request tool uses, and the
runner is a parameter so a test can read every argv this module ever builds.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from chimera.core.pull_request import run_quiet
from chimera.core.worktree import WORKTREE_PREFIX, is_inside, worktree_parents

Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]

_GIT = ("git", "-c", "core.fsmonitor=false")

#: Why a worktree was kept. Stable strings: the desktop maps each to a sentence in every language.
NOT_CHIMERA_WORKTREE = "not_chimera_worktree"
MAIN_CHECKOUT = "main_checkout"
NOT_REGISTERED = "not_registered"
DETACHED = "detached"
CURRENT_BRANCH = "current_branch"
DIRTY = "dirty"
NO_UPSTREAM = "no_upstream"
UNPUSHED = "unpushed"
GIT_FAILED = "git_failed"


@dataclass(frozen=True)
class Cleanup:
    """What happened: ``removed`` with the branch, or kept with the first reason."""

    removed: bool
    kept: str = ""
    branch: str = ""
    worktree: str = ""


def _run(runner: Runner, args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return runner([*_GIT, *args], cwd)


def _out(runner: Runner, args: Sequence[str], cwd: Path) -> str | None:
    done = _run(runner, args, cwd)
    return done.stdout.strip() if done.returncode == 0 else None


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _beside_its_repository(path: Path, runner: Runner) -> bool:
    """Whether ``path`` lies in its OWN repository's conversations' folder
    (`worktree.durable_worktree_folder`), the place a durable worktree is made in when no folder is
    configured. The repository is read from the folder's git, so a ``.chimera-worktrees`` beside
    some other repository is not a place for this one."""
    from chimera.core.worktree import durable_worktree_folder

    if not path.is_dir():
        return False
    common = _out(runner, ["rev-parse", "--path-format=absolute", "--git-common-dir"], path)
    if not common:
        return False
    beside = durable_worktree_folder(Path(common).parent)
    return beside is not None and is_inside(path, beside)


def check(
    folder: Path,
    *,
    roots: Sequence[Path] | None = None,
    runner: Runner = run_quiet,
) -> tuple[Cleanup, Path | None]:
    """Decide whether ``folder`` may go. Reads only. Returns the verdict and the main checkout."""
    path = Path(folder)
    shown = str(path)
    allowed = list(roots) if roots is not None else worktree_parents()
    if not path.name.startswith(WORKTREE_PREFIX):
        return Cleanup(False, NOT_CHIMERA_WORKTREE, worktree=shown), None
    if not any(is_inside(path, r) for r in allowed) and not (
        roots is None and _beside_its_repository(path, runner)
    ):
        return Cleanup(False, NOT_CHIMERA_WORKTREE, worktree=shown), None
    if not path.is_dir():
        return Cleanup(False, NOT_REGISTERED, worktree=shown), None
    common = _out(runner, ["rev-parse", "--path-format=absolute", "--git-common-dir"], path)
    if not common:
        return Cleanup(False, NOT_REGISTERED, worktree=shown), None
    main = Path(common).parent
    if _same(main, path):
        return Cleanup(False, MAIN_CHECKOUT, worktree=shown), None
    listed = _out(runner, ["worktree", "list", "--porcelain"], main) or ""
    registered = [
        Path(line[len("worktree ") :].strip()) for line in listed.splitlines()
        if line.startswith("worktree ")
    ]
    if not any(_same(p, path) for p in registered):
        return Cleanup(False, NOT_REGISTERED, worktree=shown), main
    branch = _out(runner, ["symbolic-ref", "--short", "-q", "HEAD"], path)
    if not branch:
        return Cleanup(False, DETACHED, worktree=shown), main
    if branch == (_out(runner, ["symbolic-ref", "--short", "-q", "HEAD"], main) or ""):
        return Cleanup(False, CURRENT_BRANCH, branch=branch, worktree=shown), main
    status = _run(runner, ["status", "--porcelain"], path)
    if status.returncode != 0:
        return Cleanup(False, GIT_FAILED, branch=branch, worktree=shown), main
    if status.stdout.strip():
        return Cleanup(False, DIRTY, branch=branch, worktree=shown), main
    upstream = _run(runner, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], path)
    if upstream.returncode != 0 or not upstream.stdout.strip():
        return Cleanup(False, NO_UPSTREAM, branch=branch, worktree=shown), main
    ahead = _out(runner, ["rev-list", "--count", "@{u}..HEAD"], path)
    if ahead is None or not ahead.isdigit():
        return Cleanup(False, GIT_FAILED, branch=branch, worktree=shown), main
    if int(ahead) > 0:
        return Cleanup(False, UNPUSHED, branch=branch, worktree=shown), main
    return Cleanup(True, branch=branch, worktree=shown), main


def remove_merged_worktree(
    folder: Path,
    *,
    roots: Sequence[Path] | None = None,
    runner: Runner = run_quiet,
) -> Cleanup:
    """Remove ``folder`` and its local branch when :func:`check` allows it; otherwise touch nothing."""
    verdict, main = check(folder, roots=roots, runner=runner)
    if not verdict.removed or main is None:
        return verdict
    if _run(runner, ["worktree", "remove", str(Path(folder))], main).returncode != 0:
        return Cleanup(False, GIT_FAILED, branch=verdict.branch, worktree=verdict.worktree)
    # The worktree is gone at this point; a branch git will not delete with -d stays, and saying so
    # is the honest result — the folder was removed, the branch was kept.
    if _run(runner, ["branch", "-d", verdict.branch], main).returncode != 0:
        return Cleanup(True, GIT_FAILED, branch=verdict.branch, worktree=verdict.worktree)
    return verdict
