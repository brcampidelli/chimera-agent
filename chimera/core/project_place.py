"""Where a Code turn opened "in a project" runs (study 03, A5).

An orchestrator driving the desktop through the MCP bridge used to pass ``workspace=<folder>``, and
to run tasks in parallel it made one ``git worktree add`` folder per task under its own scratch
directory. Each folder became a project on the owner's sidebar, and nothing in the app could ever
clean them up: the app does not undo what it did not make (`worktree_prune`, the owner's decision of
2026-10-17).

So a turn may name the PROJECT instead, and ask the app for isolation:

* ``project`` — a project the desktop already knows: a registered one (by path or alias), or the
  root of a git repository some conversation already ran in (the A1 stamp). Anything else is
  refused: the bridge must not be a way to point the agent at an arbitrary folder by calling it a
  project, and the owner adds projects in the app (or through ``projects.add``, where the bridge's
  place guard applies).
* ``worktree: "new"`` (with an optional ``branch``) — Chimera makes the worktree with its own
  `GitWorktree.create`: the ``chimera-wt-`` prefix, in Chimera's worktree folder, with the owner
  record marked durable. That is exactly what the post-merge cleanup and the cleanup dialog accept
  as Chimera's, so the folder is cleaned like any other the app made, and the conversation's stamp
  files it under the project.
* ``workspace`` still works, and with a ``project`` it must be inside it: a folder of the main
  checkout, or a checkout of the same repository. A project cannot be a door to another folder.

Every refusal is a :class:`PlaceRefused` with an HTTP status and a sentence the caller can act on.
Pure of FastAPI; the route translates.
"""

from __future__ import annotations

import os
import re
import secrets
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from chimera.core.checkout import checkout_of
from chimera.core.code_projects import RegisteredProject
from chimera.core.worktree import GitWorktree, _git, branch_prefix, is_inside

#: The one value ``worktree`` takes.
WORKTREE_NEW = "new"

#: Longest branch name accepted. Git takes more; a person reads this name in `git branch`.
MAX_BRANCH = 100

#: Longest slug of a title put into a derived branch name and the worktree's folder name.
_MAX_SLUG = 40

#: One segment of a branch name: letters, digits, `.`, `_`, `-`, starting with a letter or digit.
#: Narrower than git's rules on purpose — no `@{`, `~`, `^`, `:`, `?`, `*`, `[`, space or backslash
#: can reach `git worktree add -b`, and no segment can start with `-` and be read as an option.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: A run's throwaway branches are `<prefix>/attempt-<hex>`, and `worktree.prune_orphans` deletes
#: every such branch with no worktree on it. A conversation's branch named like that would be
#: swept as a dead run's the moment its worktree went — refused, so the two namespaces never meet.
_RUN_BRANCH = re.compile(r"(^|/)attempt-")


class PlaceRefused(Exception):
    """A turn's place was refused. ``status`` is the HTTP status the route answers with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Place:
    """Where the turn runs. ``workspace`` is the string the conversation records."""

    workspace: str
    project: str
    branch: str | None = None
    created: bool = False


def _key(path: Path | str) -> str:
    try:
        resolved = str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        resolved = str(Path(path).expanduser().absolute())
    return os.path.normcase(resolved)


def known_project(
    project: str, *, registered: Sequence[RegisteredProject], stamped: Iterable[str]
) -> str:
    """The project ``project`` names, as the string the desktop already knows it by.

    A registered project matches by path or alias; a hidden one is refused (the owner removed it
    from the lists, and opening a conversation there from outside would bring it back). Otherwise
    the path must be the repository root a conversation's checkout stamp recorded — ``stamped`` is
    those roots. The answer is the KNOWN spelling, not the caller's, so the conversation lands in
    the sidebar group that already exists.
    """
    text = (project or "").strip()
    if not text:
        raise PlaceRefused(422, "project must not be empty")
    by_alias = [row for row in registered if row.alias and row.alias == text]
    if len(by_alias) == 1:
        text = by_alias[0].path
    folder = Path(text).expanduser()
    if not folder.is_absolute():
        raise PlaceRefused(
            400, f"unknown project {project!r}: pass the absolute path or the alias of a project"
        )
    if not folder.is_dir():
        raise PlaceRefused(400, f"project not found: {text}")
    key = _key(folder)
    for row in registered:
        if _key(row.path) == key:
            if row.hidden:
                raise PlaceRefused(
                    403, f"the owner hid the project {row.path}; it is shown again from the app"
                )
            return row.path
    for root in stamped:
        if root and _key(root) == key:
            return root
    raise PlaceRefused(
        400,
        f"unknown project: {text} is neither a registered project nor the root of a repository a "
        "conversation already ran in; add it in the app (or with projects.add) first",
    )


def check_branch(name: str) -> str:
    """``name`` if it is a branch name this module will hand to git, else :class:`PlaceRefused`."""
    branch = (name or "").strip()
    if not branch:
        raise PlaceRefused(422, "branch must not be empty")
    if len(branch) > MAX_BRANCH:
        raise PlaceRefused(422, f"branch is longer than {MAX_BRANCH} characters")
    segments = branch.split("/")
    bad = (
        any(not _SEGMENT.fullmatch(s) for s in segments)
        or ".." in branch
        or any(s.endswith((".", ".lock")) for s in segments)
        or branch == "HEAD"
    )
    if bad:
        raise PlaceRefused(
            422,
            f"bad branch name {name!r}: segments of letters, digits, '.', '_' and '-' separated "
            "by '/', each starting with a letter or digit, no '..' and none ending in '.' or '.lock'",
        )
    if _RUN_BRANCH.search(branch):
        raise PlaceRefused(
            422,
            f"bad branch name {name!r}: '<prefix>/attempt-…' is the name of a run's throwaway "
            "branch, which the cleanup deletes",
        )
    return branch


def slug(text: str) -> str:
    """``text`` as lowercase ASCII words joined by ``-``, at most 40 characters; may be empty."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    words = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    if len(words) > _MAX_SLUG:
        words = words[:_MAX_SLUG].rsplit("-", 1)[0] if "-" in words[:_MAX_SLUG] else words[:_MAX_SLUG]
    return words.strip("-")


def derived_branch(title: str | None) -> str:
    """``<CHIMERA_BRANCH_PREFIX>/<slug of the title>-<6 hex>``: readable, and never taken twice."""
    words = slug(title or "") or "session"
    if words.startswith("attempt"):
        words = f"task-{words}"
    return check_branch(f"{branch_prefix()}/{words}-{secrets.token_hex(3)}")


def _same_repository(folder: Path, main: str) -> bool:
    found = checkout_of(folder)
    return found is not None and found.kind != "plain" and _key(found.project_root) == _key(main)


def _new_worktree(main: str, branch: str | None, title: str | None) -> Place:
    name = check_branch(branch) if branch else derived_branch(title)
    exists = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], Path(main))
    if exists.returncode == 0:
        raise PlaceRefused(409, f"branch {name} already exists in {main}; pick another name")
    try:
        made = GitWorktree.create(
            Path(main), branch=name, durable=True, label=slug(name.rsplit("/", 1)[-1])[:30]
        )
    except (RuntimeError, OSError) as exc:
        raise PlaceRefused(409, f"could not make the worktree: {exc}") from exc
    return Place(workspace=str(made.path), project=main, branch=name, created=True)


def resolve_place(
    project: str,
    *,
    workspace: str | None = None,
    stored: str | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    title: str | None = None,
    continuing: bool = False,
) -> Place:
    """Where a turn naming ``project`` (already :func:`known_project`) runs.

    ``stored`` is the folder the conversation already runs in, when the turn continues one
    (``continuing``) without naming a workspace. A new worktree only STARTS a conversation: to keep
    working in it, the caller sends the ``session_id`` back (and may repeat the ``workspace`` it got).
    """
    if worktree not in (None, "", WORKTREE_NEW):
        raise PlaceRefused(422, f'worktree takes only "{WORKTREE_NEW}"')
    if branch and worktree != WORKTREE_NEW:
        raise PlaceRefused(422, f'branch is only read with worktree: "{WORKTREE_NEW}"')
    found = checkout_of(project)
    git_project = found is not None and found.kind != "plain"
    main = found.project_root if found is not None and git_project else project
    current = found.branch if found is not None else None
    if worktree == WORKTREE_NEW:
        if workspace:
            raise PlaceRefused(422, 'worktree: "new" makes the folder; do not pass a workspace too')
        if continuing:
            raise PlaceRefused(
                422,
                'worktree: "new" starts a conversation; continue one with its session_id alone',
            )
        if not git_project:
            raise PlaceRefused(400, f"{project} is not a git repository; it has no worktrees")
        return _new_worktree(main, branch, title)
    named = workspace or (stored if continuing else None)
    if not named:
        return Place(workspace=project, project=main, branch=current)
    folder = Path(named).expanduser()
    if not folder.is_absolute():
        folder = Path(project) / folder
    if not folder.is_dir():
        raise PlaceRefused(400, f"workspace not found: {named}")
    if not (is_inside(folder, Path(project)) or (git_project and _same_repository(folder, main))):
        whose = "this conversation runs in" if not workspace else "workspace"
        raise PlaceRefused(400, f"{whose} {named}, which is outside the project {project}")
    inner = checkout_of(folder)
    # The caller's own spelling when it gave an absolute one (the list groups by the recorded
    # string, `code_session`), the joined path for a relative one.
    recorded = named if Path(named).expanduser().is_absolute() else str(folder)
    return Place(
        workspace=recorded, project=main, branch=inner.branch if inner is not None else None
    )
