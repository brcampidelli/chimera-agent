"""The worktrees of one repository, which of them may go, and removing the ones the owner picks.

The Code screen's "clean up finished worktrees" dialog. Like `pull_request_cleanup.py`, whose
refusals this generalises, it is written as a list of refusals with removal at the end, and every
refusal leaves the disk exactly as it was. A worktree is removable only when NONE of these holds:

* **It is the main checkout**, or git does not list it (a path outside the repository's worktrees).
* **Its folder is gone** (git marks it ``prunable``): there is nothing on disk to remove.
* **Chimera did not make it.** The owner's decision of 2026-10-17: the interface removes only what
  Chimera created. Ownership is the record Chimera already keeps, all three parts of it — the
  folder's name starts with ``chimera-wt-``, it lies in a folder worktrees are made in
  (`worktree.worktree_parents`), and its git admin directory holds the owner file `GitWorktree.create`
  writes (`worktree.OWNER_FILE`). Everything else — the owner's ``w-*`` folders, one another tool
  made — is LISTED with :data:`NOT_CHIMERA_WORKTREE` and never removable from here, whatever the
  request says. There is deliberately no second registry: git's admin directory is already the
  durable record, and two records can disagree (`worktree.prune_orphans` explains the same choice).
* **It is locked** (``git worktree lock``).
* **Something is using it**: a run of this process is working in it, the process that made it is
  still running (or cannot be shown to have stopped), or the caller says a turn, a queued message, a
  background work or a waiting question belongs to it.
* **Its HEAD is detached**, or its branch is the one the main checkout has out.
* **Something would be lost**: ``git status --porcelain`` is not empty (untracked files count), or
  the branch is NOT merged into the default branch and has no upstream or commits not pushed to it.

What it never does: ``--force``, ``git branch -D``, push, or touch a remote. The worktree goes with
``git worktree remove`` (git refuses a dirty tree a second time), the branch with ``git branch -d``
(git refuses a branch merged nowhere), then ``git worktree prune``. A Windows file held open makes
``worktree remove`` fail; that is reported, never retried harder.

Each refusal is one named function in :data:`REFUSALS`, so the tests can switch one off and show
that the test guarding it then fails.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from chimera.core.pull_request import run_quiet
from chimera.core.pull_request_cleanup import (
    CURRENT_BRANCH,
    DETACHED,
    DIRTY,
    GIT_FAILED,
    MAIN_CHECKOUT,
    NO_UPSTREAM,
    NOT_CHIMERA_WORKTREE,
    NOT_REGISTERED,
    UNPUSHED,
    Runner,
)
from chimera.core.worktree import WORKTREE_PREFIX, is_inside, worktree_parents

#: `--no-optional-locks`: a plain `git status` refreshes the index when it can take the lock, and the
#: listing and the dry run promise to leave every byte of the repository as it was.
_GIT = ("git", "--no-optional-locks", "-c", "core.fsmonitor=false")

#: Reasons this module adds to `pull_request_cleanup`'s. Stable strings, mapped in every language.
MISSING = "missing"
LOCKED = "locked"
IN_USE = "in_use"

Busy = Callable[[Path], bool]


@dataclass(frozen=True)
class WorktreeRow:
    """One worktree as the dialog shows it. ``kept`` is "" when it may be removed."""

    path: str
    branch: str = ""
    head: str = ""
    main: bool = False
    exists: bool = True
    locked: bool = False
    owned: bool = False
    in_use: bool = False
    merged: bool = False
    clean: bool = True
    untracked: tuple[str, ...] = ()
    upstream: str = ""
    ahead: int | None = None
    status_failed: bool = False
    #: Its branch is the one the main checkout has out.
    main_branch: bool = False
    kept: str = ""

    @property
    def removable(self) -> bool:
        return not self.kept


@dataclass(frozen=True)
class PruneResult:
    """What happened to one requested path. ``removed`` is False on a dry run."""

    path: str
    kept: str = ""
    branch: str = ""
    removed: bool = False
    would_remove: bool = False
    branch_deleted: bool = False


@dataclass
class _Listing:
    main: Path
    rows: list[WorktreeRow] = field(default_factory=list)


def _run(runner: Runner, args: Sequence[str], cwd: Path) -> tuple[int, str]:
    done = runner([*_GIT, *args], cwd)
    return done.returncode, (done.stdout or "")


def _norm(path: Path | str) -> str:
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = str(Path(path).absolute())
    return os.path.normcase(resolved)


def main_checkout(project: Path, runner: Runner = run_quiet) -> Path | None:
    """The main checkout of the repository ``project`` is in (itself, or the one it is a worktree of)."""
    if not Path(project).is_dir():
        return None
    code, out = _run(runner, ["rev-parse", "--path-format=absolute", "--git-common-dir"], project)
    if code != 0 or not out.strip():
        return None
    common = Path(out.strip())
    return common.parent if common.name == ".git" else None


def _porcelain(main: Path, runner: Runner) -> list[dict[str, str]] | None:
    code, out = _run(runner, ["worktree", "list", "--porcelain"], main)
    if code != 0:
        return None
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value.strip()
    if current:
        blocks.append(current)
    return [b for b in blocks if "worktree" in b]


def _owner_record(path: Path) -> dict[str, object] | None:
    """The owner file `GitWorktree.create` writes in the worktree's git admin directory, or None."""
    from chimera.core.worktree import OWNER_FILE, _admin_dir

    admin = _admin_dir(path)
    if admin is None:
        return None
    try:
        record = json.loads((admin / OWNER_FILE).read_text(encoding="utf-8"))
        int(record["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return record if isinstance(record, dict) else None


def is_chimera_worktree(path: Path, roots: Sequence[Path] | None = None) -> bool:
    """Whether Chimera made this worktree: name, place, and the owner record — all three."""
    allowed = list(roots) if roots is not None else worktree_parents()
    if not path.name.startswith(WORKTREE_PREFIX):
        return False
    if not any(is_inside(path, root) for root in allowed):
        return False
    return _owner_record(path) is not None


def _maker_busy(path: Path) -> bool:
    """A run is working in it: this process's, or the still-running (or unknowable) process that
    made it. A worktree this process made and is not running in is free — that is what a worktree
    kept past its run looks like."""
    from chimera.core.worktree import _live_here, _owner_state

    try:
        if path.resolve() in _live_here:
            return True
    except OSError:
        return True
    record = _owner_record(path)
    if record is None:
        return False
    if int(str(record["pid"])) == os.getpid():
        return False
    return _owner_state(record) != "gone"


def _default_refs(main: Path, runner: Runner) -> list[str]:
    refs: list[str] = []
    code, out = _run(runner, ["symbolic-ref", "-q", "refs/remotes/origin/HEAD"], main)
    if code == 0 and out.strip():
        refs.append(out.strip())
    code, out = _run(runner, ["symbolic-ref", "-q", "HEAD"], main)
    if code == 0 and out.strip():
        refs.append(out.strip())
    return refs


def _describe(
    block: dict[str, str],
    *,
    is_main: bool,
    main_branch: str,
    defaults: list[str],
    roots: Sequence[Path] | None,
    runner: Runner,
    busy: Busy,
) -> WorktreeRow:
    """Everything the refusals read, gathered by reading only."""
    path = Path(block["worktree"])
    ref = block.get("branch", "")
    branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ""
    row = WorktreeRow(
        path=str(path),
        branch=branch,
        head=block.get("HEAD", ""),
        main=is_main,
        exists=path.is_dir() and "prunable" not in block,
        locked="locked" in block,
    )
    if is_main or not row.exists:
        return row
    owned = is_chimera_worktree(path, roots)
    in_use = (owned and _maker_busy(path)) or busy(path)
    code, out = _run(runner, ["status", "--porcelain"], path)
    lines = [ln for ln in out.splitlines() if ln.strip()] if code == 0 else []
    untracked = tuple(ln[3:] for ln in lines if ln.startswith("??"))
    tracked = [ln for ln in lines if not ln.startswith("??")]
    upstream, ahead, merged = "", None, False
    if branch:
        code_u, out_u = _run(
            runner, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{u}}"], path
        )
        upstream = out_u.strip() if code_u == 0 else ""
        if upstream:
            code_a, out_a = _run(runner, ["rev-list", "--count", f"{branch}@{{u}}..{branch}"], path)
            ahead = int(out_a.strip()) if code_a == 0 and out_a.strip().isdigit() else None
        merged = any(
            _run(runner, ["merge-base", "--is-ancestor", f"refs/heads/{branch}", d], path)[0] == 0
            for d in defaults
            if d != f"refs/heads/{branch}"
        )
    return replace(
        row,
        owned=owned,
        in_use=in_use,
        merged=merged,
        clean=code == 0 and not tracked,
        untracked=untracked,
        upstream=upstream,
        ahead=ahead,
        status_failed=code != 0,
        main_branch=bool(branch) and branch == main_branch,
    )


# --- the refusals, in the order they are reported -------------------------------------------------
# Each reads a described row and returns the reason it is kept, or "".


def _refuse_main(row: WorktreeRow) -> str:
    return MAIN_CHECKOUT if row.main else ""


def _refuse_missing(row: WorktreeRow) -> str:
    return MISSING if not row.exists else ""


def _refuse_external(row: WorktreeRow) -> str:
    return NOT_CHIMERA_WORKTREE if not row.owned else ""


def _refuse_locked(row: WorktreeRow) -> str:
    return LOCKED if row.locked else ""


def _refuse_in_use(row: WorktreeRow) -> str:
    return IN_USE if row.in_use else ""


def _refuse_detached(row: WorktreeRow) -> str:
    return DETACHED if not row.branch else ""


def _refuse_current_branch(row: WorktreeRow) -> str:
    return CURRENT_BRANCH if row.main_branch else ""


def _refuse_dirty(row: WorktreeRow) -> str:
    if row.status_failed:
        return GIT_FAILED
    return DIRTY if not row.clean or row.untracked else ""


def _refuse_unpushed(row: WorktreeRow) -> str:
    """Not merged anywhere AND not safely on its upstream. "I cannot tell whether this was sent" is
    not "this was sent", so no upstream counts as unpushed."""
    if row.merged:
        return ""
    if not row.upstream:
        return NO_UPSTREAM
    if row.ahead is None:
        return GIT_FAILED
    return UNPUSHED if row.ahead > 0 else ""


REFUSALS: list[tuple[str, Callable[[WorktreeRow], str]]] = [
    ("main_checkout", _refuse_main),
    ("missing", _refuse_missing),
    ("external", _refuse_external),
    ("locked", _refuse_locked),
    ("in_use", _refuse_in_use),
    ("detached", _refuse_detached),
    ("current_branch", _refuse_current_branch),
    ("dirty", _refuse_dirty),
    ("unpushed", _refuse_unpushed),
]


def _verdict(row: WorktreeRow) -> WorktreeRow:
    for _name, refuse in REFUSALS:
        reason = refuse(row)
        if reason:
            return replace(row, kept=reason)
    return replace(row, kept="")


def _never_busy(_path: Path) -> bool:
    return False


def _listing(
    project: Path, *, roots: Sequence[Path] | None, runner: Runner, busy: Busy
) -> _Listing | None:
    main = main_checkout(project, runner)
    if main is None:
        return None
    blocks = _porcelain(main, runner)
    if blocks is None:
        return None
    main_key = _norm(main)
    code, out = _run(runner, ["symbolic-ref", "--short", "-q", "HEAD"], main)
    main_branch = out.strip() if code == 0 else ""
    defaults = _default_refs(main, runner)
    listing = _Listing(main=main)
    for block in blocks:
        described = _describe(
            block,
            is_main=_norm(block["worktree"]) == main_key,
            main_branch=main_branch,
            defaults=defaults,
            roots=roots,
            runner=runner,
            busy=busy,
        )
        listing.rows.append(_verdict(described))
    return listing


def list_worktrees(
    project: Path,
    *,
    roots: Sequence[Path] | None = None,
    runner: Runner = run_quiet,
    busy: Busy = _never_busy,
) -> tuple[Path, list[WorktreeRow]] | None:
    """Every worktree of ``project``'s repository, main checkout first. Reads only. None when
    ``project`` is not in a git repository."""
    listing = _listing(Path(project), roots=roots, runner=runner, busy=busy)
    return None if listing is None else (listing.main, listing.rows)


def prune_worktrees(
    project: Path,
    paths: Sequence[str],
    *,
    dry_run: bool = True,
    roots: Sequence[Path] | None = None,
    runner: Runner = run_quiet,
    busy: Busy = _never_busy,
) -> list[PruneResult] | None:
    """Remove the requested worktrees that every refusal allows. ``dry_run`` (the default) reads
    only and says what WOULD go. None when ``project`` is not in a git repository.

    The listing is read again here, immediately before acting, so a verdict the screen showed a
    minute ago is never what is acted on."""
    listing = _listing(Path(project), roots=roots, runner=runner, busy=busy)
    if listing is None:
        return None
    by_path = {_norm(row.path): row for row in listing.rows}
    results: list[PruneResult] = []
    removed_any = False
    for requested in paths:
        row = by_path.get(_norm(requested))
        if row is None:
            results.append(PruneResult(path=str(requested), kept=NOT_REGISTERED))
            continue
        if row.kept:
            results.append(PruneResult(path=row.path, kept=row.kept, branch=row.branch))
            continue
        if dry_run:
            results.append(PruneResult(path=row.path, branch=row.branch, would_remove=True))
            continue
        code, _ = _run(runner, ["worktree", "remove", row.path], listing.main)
        if code != 0:
            results.append(PruneResult(path=row.path, kept=GIT_FAILED, branch=row.branch))
            continue
        removed_any = True
        deleted = _run(runner, ["branch", "-d", row.branch], listing.main)[0] == 0
        results.append(
            PruneResult(path=row.path, branch=row.branch, removed=True, branch_deleted=deleted)
        )
    if removed_any:
        _run(runner, ["worktree", "prune"], listing.main)
    return results
