"""Which repository — and which branch of it — a folder is a checkout of, read from ``.git`` alone.

A Code conversation records the folder it runs in, as the caller spelled it, and the sidebar groups
by that string. That is right for folders and wrong for git worktrees: an orchestrator that runs
tasks in parallel gives each one its own ``git worktree add`` folder, so one repository showed up as
twenty-two top-level "projects" (study 03, measured on the owner's disk). Git already declares that
those folders are one repository — every linked worktree points at the same common directory — and
this module reads that declaration so the conversation can carry it.

Read, not asked: no ``git`` subprocess. The facts needed are three small files, and the place that
needs them is a turn's start and the conversation list, where a process per folder would be paid
on every look. What is read, and why it is enough:

* ``<folder>/.git`` is a DIRECTORY in a main checkout, and a FILE ``gitdir: <path>`` in a linked
  worktree (and in a submodule). The path may be relative to the folder holding the file.
* ``<gitdir>/commondir`` exists only in a linked worktree's git dir and names the repository's
  shared directory, again possibly relative to the git dir.
* ``<gitdir>/HEAD`` is per checkout: ``ref: refs/heads/<branch>``, or a bare commit id when HEAD is
  detached.

Two separate clones of one remote are two projects here, and that is deliberate: they have separate
object stores, separate branches and separate working trees, and merging them because their
``origin`` URL matched would file a conversation under a checkout that does not hold its files.
Nothing is merged unless git itself says the folders share a repository.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args

_log = logging.getLogger(__name__)

CheckoutKind = Literal["main", "worktree", "plain"]
_KINDS: frozenset[str] = frozenset(get_args(CheckoutKind))

#: Bound on any one of the files read here. They are a line each; anything larger is not what it
#: claims to be, and reading it whole would let a stray file make the conversation list slow.
_MAX_READ = 4096


@dataclass(frozen=True)
class Checkout:
    """What a folder is, as far as grouping conversations needs to know.

    ``project_root`` is the key a list groups by: the main working tree of the repository for both
    a main checkout and any of its linked worktrees, or the folder itself when it is not in a git
    repository. ``common_dir`` is the repository's shared git directory (None for ``plain``).
    ``branch`` is None when HEAD is detached, unreadable, or there is no repository.
    """

    project_root: str
    common_dir: str | None
    branch: str | None
    kind: CheckoutKind

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "common_dir": self.common_dir,
            "branch": self.branch,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: object) -> Checkout | None:
        """The stamp a conversation file carries, or None when it has none or it does not parse.

        Defensive because the file outlives this code: an older file has no stamp at all, and a
        hand-edited or future one may carry a shape this version does not know. Neither may stop
        the conversation from being listed.
        """
        if not isinstance(data, dict):
            return None
        root = data.get("project_root")
        kind = data.get("kind")
        if not isinstance(root, str) or not root or kind not in _KINDS:
            return None
        common = data.get("common_dir")
        branch = data.get("branch")
        return cls(
            project_root=root,
            common_dir=common if isinstance(common, str) and common else None,
            branch=branch if isinstance(branch, str) and branch else None,
            kind=cast(CheckoutKind, kind),
        )

    @property
    def name(self) -> str:
        """The project's display name: the basename of its root (the whole root for a drive)."""
        return Path(self.project_root).name or self.project_root


def _read_small(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_READ)
    except OSError:
        return None


def _absolute(base: Path, text: str) -> Path:
    """``text`` as a path, relative to ``base`` when it is not absolute, normalised without
    requiring it to exist (``strict=False``): a worktree's admin dir may be checked next."""
    target = Path(text.strip())
    if not target.is_absolute():
        target = base / target
    return target.resolve(strict=False)


def _gitdir_of(dotgit: Path) -> Path | None:
    """The git dir a ``.git`` entry stands for: the entry itself when it is a directory, the
    target of its ``gitdir:`` line when it is a file, or None when it is neither readable form."""
    if dotgit.is_dir():
        return dotgit
    text = _read_small(dotgit)
    if text is None:
        return None
    first = text.splitlines()[0] if text.splitlines() else ""
    if not first.startswith("gitdir:"):
        return None
    pointed = first[len("gitdir:"):].strip()
    return _absolute(dotgit.parent, pointed) if pointed else None


def _branch_of(gitdir: Path) -> str | None:
    head = _read_small(gitdir / "HEAD")
    if head is None:
        return None
    line = head.strip()
    if not line.startswith("ref:"):
        return None  # a bare commit id: HEAD is detached, which has no branch to show
    ref = line[len("ref:"):].strip()
    prefix = "refs/heads/"
    return ref[len(prefix):] if ref.startswith(prefix) else (ref or None)


def _main_worktree_of(common_dir: Path) -> Path:
    """The main working tree of a repository whose shared directory is ``common_dir``.

    For an ordinary repository that is the folder holding ``.git``. A bare repository (worktrees
    added to ``repo.git``) has no main working tree, and the repository directory itself is the
    only stable name the project has — so it stands in.
    """
    return common_dir.parent if common_dir.name == ".git" else common_dir


def checkout_of(path: str | Path) -> Checkout | None:
    """What ``path`` is a checkout of, or None when that cannot be told from what is on disk.

    None for a folder that does not exist (the caller keeps whatever it learned before — a removed
    worktree must stay in its project) and for a ``.git`` link that does not resolve (a worktree
    whose admin directory was pruned: git no longer declares it part of anything, and calling it
    ``plain`` would overwrite the last good answer with a guess).

    A subfolder answers for the checkout that contains it, found by walking up to the nearest
    ``.git`` — the same rule git applies. A folder with no ``.git`` above it is ``plain`` and is its
    own project.
    """
    try:
        folder = Path(path).expanduser().resolve(strict=False)
        if not folder.is_dir():
            return None
    except (OSError, RuntimeError, ValueError):  # unresolvable spelling, symlink loop, NUL byte
        return None
    for here in (folder, *folder.parents):
        dotgit = here / ".git"
        try:
            present = dotgit.exists()
        except OSError:
            present = False
        if not present:
            continue
        return _checkout_at(here, dotgit)
    return Checkout(project_root=str(folder), common_dir=None, branch=None, kind="plain")


def _checkout_at(toplevel: Path, dotgit: Path) -> Checkout | None:
    gitdir = _gitdir_of(dotgit)
    if gitdir is None or not gitdir.is_dir():
        _log.debug("%s: .git does not lead to a git dir; checkout unknown", toplevel)
        return None
    branch = _branch_of(gitdir)
    commondir_text = _read_small(gitdir / "commondir")
    if commondir_text is None or not commondir_text.strip():
        # A main checkout — or a submodule, whose ``.git`` file points into the superproject's
        # ``modules/`` with no ``commondir``: either way this working tree IS the repository's own.
        return Checkout(
            project_root=str(toplevel), common_dir=str(gitdir), branch=branch, kind="main"
        )
    common = _absolute(gitdir, commondir_text.splitlines()[0])
    if not common.is_dir():
        _log.debug("%s: commondir %s is missing; checkout unknown", toplevel, common)
        return None
    return Checkout(
        project_root=str(_main_worktree_of(common)),
        common_dir=str(common),
        branch=branch,
        kind="worktree",
    )
