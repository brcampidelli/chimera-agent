"""Structured git helpers for the Code screen: status / diff / commit / scoped revert.

Thin, honest wrappers over :func:`chimera.core.worktree._git` (list-arg subprocess, no shell, 60 s
timeout, non-zero inspected — never raised). EVERY helper gates on
:func:`~chimera.core.worktree.is_git_repo` FIRST, because ``_git`` itself does not catch a missing
``git`` binary — the gate does (``FileNotFoundError`` → ``False``). So on a machine without git, or a
folder that isn't a repo, each helper returns the honest ``{is_repo: False}`` empty-state instead of
crashing the endpoint with a 500.

The CLI stays sovereign: this is a convenience view. ``POST /api/fs/exec`` already runs arbitrary
shell in the workspace, so a structured git endpoint is not a new trust escalation. Nothing here is
fabricated — real ``git`` stdout/stderr and real exit codes only. ``commit`` stages EXPLICIT paths
(never ``add -A``); ``revert`` is scoped to the passed paths (never workspace-wide) and only touches
what git can (tracked modifications/deletions + untracked files it created among those paths).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.worktree import _git, is_git_repo

_MAX_OUTPUT = 4000  # bound the combined git output echoed back to the UI
_MAX_ERR = 500  # bound a short error string


def _combined(*results: Any) -> str:
    """Join the stdout+stderr of one or more git runs into a single trimmed string."""
    parts = [(r.stdout + r.stderr).strip() for r in results]
    return "\n".join(p for p in parts if p).strip()


def _parse_branch(text: str) -> str:
    """Extract the current branch name from a porcelain ``## `` header body.

    Handles ``main``, ``main...origin/main``, ``main...origin/main [ahead 1]``,
    ``No commits yet on main`` (a fresh repo), and ``HEAD (no branch)`` (detached) — defensively.
    """
    text = text.strip()
    marker = "No commits yet on "
    if text.startswith(marker):
        return text[len(marker) :].strip()
    text = text.split("...", 1)[0]  # drop upstream tracking (main...origin/main)
    return text.split(" ", 1)[0].strip()  # drop " [ahead 1]" / "(no branch)"


def _unquote(path: str) -> str:
    """Strip the surrounding double quotes git adds for a path with special chars (quotepath on)."""
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        return path[1:-1]
    return path


def git_status(ws: Path) -> dict[str, Any]:
    """The porcelain working-tree status, or the honest ``{is_repo: False}`` empty-state.

    Parses ``git status --porcelain=v1 --branch``: the ``## `` line gives the branch, each remaining
    line is ``XY <path>`` where ``x`` is the index status and ``y`` the worktree status (``??`` =
    untracked). A rename line (``orig -> new``) reports the new path.
    """
    if not is_git_repo(ws):
        return {"is_repo": False, "branch": "", "files": []}
    result = _git(["status", "--porcelain=v1", "--branch"], Path(ws))
    branch = ""
    files: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if line.startswith("## "):
            branch = _parse_branch(line[3:])
            continue
        if len(line) < 3:
            continue
        code = line[:2]
        x, y = code[0], code[1]
        untracked = code == "??"
        rest = line[3:]
        path = rest.split(" -> ", 1)[1] if " -> " in rest else rest
        files.append(
            {
                "path": _unquote(path),
                "x": x,
                "y": y,
                "staged": (not untracked) and x != " ",
                "untracked": untracked,
            }
        )
    return {"is_repo": True, "branch": branch, "files": files}


def git_diff(ws: Path, *, path: str | None = None, staged: bool = False) -> dict[str, Any]:
    """The real unified diff (``git diff [--cached] [-- <path>]``), or ``{is_repo: False}``.

    ``patch`` is the raw unified-diff body (``@@`` hunks, ``+``/``-`` lines); it is ``""`` when there
    is no diff. ``staged=True`` diffs the index against HEAD; ``path`` scopes it to one file.
    """
    if not is_git_repo(ws):
        return {"is_repo": False, "patch": ""}
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", path]
    result = _git(args, Path(ws))
    return {"is_repo": True, "patch": result.stdout}


def git_commit(ws: Path, message: str, paths: list[str]) -> dict[str, Any]:
    """Stage the EXPLICIT ``paths`` and commit them with ``message`` (never ``add -A``).

    Requires a non-empty message and at least one path. Returns ``{ok, commit, output, error}``:
    on success ``commit`` is the short HEAD hash and ``error`` is ``None``; on a non-zero git exit
    ``ok`` is ``False`` and ``error`` carries the short git stderr.
    """
    if not is_git_repo(ws):
        return {"ok": False, "commit": "", "output": "", "error": "not a git repo"}
    if not message.strip():
        return {"ok": False, "commit": "", "output": "", "error": "empty commit message"}
    if not paths:
        return {"ok": False, "commit": "", "output": "", "error": "no paths selected"}
    add = _git(["add", "--", *paths], Path(ws))
    if add.returncode != 0:
        return {
            "ok": False,
            "commit": "",
            "output": _combined(add)[:_MAX_OUTPUT],
            "error": (add.stderr.strip() or "git add failed")[:_MAX_ERR],
        }
    commit = _git(["commit", "-m", message], Path(ws))
    output = _combined(commit)[:_MAX_OUTPUT]
    if commit.returncode != 0:
        return {
            "ok": False,
            "commit": "",
            "output": output,
            "error": (commit.stderr.strip() or commit.stdout.strip() or "git commit failed")[:_MAX_ERR],
        }
    rev = _git(["rev-parse", "--short", "HEAD"], Path(ws))
    return {
        "ok": True,
        "commit": rev.stdout.strip() if rev.returncode == 0 else "",
        "output": output,
        "error": None,
    }


def git_revert_paths(ws: Path, paths: list[str]) -> dict[str, Any]:
    """Discard a run's changes, SCOPED to ``paths`` (never workspace-wide).

    Reverts what git can among the given paths: ``git checkout -- <tracked paths>`` restores tracked
    modifications/deletions, and ``git clean -fd -- <paths>`` removes untracked files the run created
    among them. Checkout runs ONLY on the paths git tracks (via ``ls-files``) because a single
    untracked pathspec makes ``git checkout`` abort the whole batch — leaving the tracked ones
    un-reverted. It does NOT touch files git ignores or can't track. Returns ``{ok, reverted, error}``.
    """
    if not is_git_repo(ws):
        return {"ok": False, "reverted": [], "error": "not a git repo"}
    if not paths:
        return {"ok": False, "reverted": [], "error": "no paths given"}
    root = Path(ws)
    tracked = [
        line for line in _git(["ls-files", "--", *paths], root).stdout.splitlines() if line.strip()
    ]
    if tracked:
        _git(["checkout", "--", *tracked], root)  # restore tracked modifications/deletions
    clean = _git(["clean", "-fd", "--", *paths], root)  # remove run-created untracked files in-scope
    if clean.returncode != 0:
        return {"ok": False, "reverted": [], "error": (clean.stderr.strip() or "git clean failed")[:_MAX_ERR]}
    return {"ok": True, "reverted": list(paths), "error": None}
