"""Structured git helpers for the Code screen: init / status / diff / commit / scoped revert.

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

import subprocess
from pathlib import Path
from typing import Any

from chimera.core.worktree import _git, is_git_repo

_MAX_OUTPUT = 4000  # bound the combined git output echoed back to the UI
_MAX_ERR = 500  # bound a short error string

#: The message on the commit :func:`git_init` makes. Says what the commit is FOR, because the person
#: reading it in six months is reading a log entry for a commit they did not type.
_SNAPSHOT_MESSAGE = "Snapshot before letting an agent edit this folder"

#: The identity a snapshot commit is authored with when the machine has none configured.
#:
#: `git commit` REFUSES on a machine with no `user.email` ("Please tell me who you are"), which on a
#: freshly installed laptop is the common case, not the exotic one. A button that answers "now go
#: configure git in a terminal" has exactly the failure this feature exists to remove, so the commit
#: falls back to an identity of its own — and only when there is none to respect, because attributing
#: someone's snapshot to "Chimera" when git knows their name would be the app overwriting a fact.
_FALLBACK_NAME = "Chimera"
_FALLBACK_EMAIL = "chimera@localhost"


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


def _identity_args(root: Path) -> list[str]:
    """``-c user.*`` overrides for the snapshot commit, or nothing when git already knows who you are."""
    email = _git(["config", "user.email"], root)
    name = _git(["config", "user.name"], root)
    if email.returncode == 0 and email.stdout.strip() and name.returncode == 0 and name.stdout.strip():
        return []
    return ["-c", f"user.name={_FALLBACK_NAME}", "-c", f"user.email={_FALLBACK_EMAIL}"]


def git_init(ws: Path) -> dict[str, Any]:
    """``git init`` in ``ws`` plus one commit of whatever is already there.

    The commit is the point. ``git init`` alone leaves a repo with no HEAD, so the isolation the
    batch runner wants exists but there is still nothing to go back TO — and the moment after this
    button is pressed is the moment the agent is given write and shell access to the folder. The
    snapshot is the return ticket, taken before the ride rather than after it.

    Refuses when the folder is ALREADY inside a repo. ``is_git_repo`` is true for a subdirectory of
    one, so this also refuses to nest a second repository inside somebody's checkout — which reads as
    a helpful button and behaves as a directory that quietly stops being part of its project.

    An empty folder is a success with an empty ``commit``: the repo is initialised and there was
    nothing to snapshot, which is not a failure and must not be reported as one (``git commit`` with
    an empty index exits non-zero, so this is checked rather than inferred from the exit code).

    Returns ``{ok, commit, output, error}`` — the shape :func:`git_commit` returns, because the UI
    reports both the same way.
    """
    root = Path(ws)
    if is_git_repo(root):
        return {"ok": False, "commit": "", "output": "", "error": "already a git repo"}
    try:
        init = _git(["init"], root)
        if init.returncode != 0:
            return {
                "ok": False,
                "commit": "",
                "output": _combined(init)[:_MAX_OUTPUT],
                "error": (init.stderr.strip() or "git init failed")[:_MAX_ERR],
            }
        add = _git(["add", "-A"], root)
        staged = _git(["diff", "--cached", "--name-only"], root)
    except (OSError, subprocess.SubprocessError) as exc:
        # This is the ONE helper in this module that runs git AFTER `is_git_repo` has said no — and
        # "no" is also what a machine with no git installed answers. Every other helper returns its
        # empty-state at that point and never reaches a subprocess; this one proceeds, so it is the
        # only one that has to catch what `_git` does not:
        #
        #   * `FileNotFoundError` (an OSError, NOT a SubprocessError — checked, because catching only
        #     `subprocess.SubprocessError` here would turn "git is not installed" into a 500 that
        #     reads as the app being broken rather than as a missing tool);
        #   * `TimeoutExpired` from the 60 s cap, which this helper can genuinely hit where the
        #     others cannot: `add -A` over a folder nobody has ever indexed is minutes of work on a
        #     home directory.
        return {"ok": False, "commit": "", "output": "", "error": f"git failed: {exc}"[:_MAX_ERR]}
    if not staged.stdout.strip():
        return {"ok": True, "commit": "", "output": _combined(init, add)[:_MAX_OUTPUT], "error": None}
    commit = _git([*_identity_args(root), "commit", "-m", _SNAPSHOT_MESSAGE], root)
    output = _combined(init, commit)[:_MAX_OUTPUT]
    if commit.returncode != 0:
        return {
            "ok": False,
            "commit": "",
            "output": output,
            "error": (commit.stderr.strip() or commit.stdout.strip() or "git commit failed")[:_MAX_ERR],
        }
    rev = _git(["rev-parse", "--short", "HEAD"], root)
    return {
        "ok": True,
        "commit": rev.stdout.strip() if rev.returncode == 0 else "",
        "output": output,
        "error": None,
    }


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
