"""Git-worktree isolation for autonomous attempts (HORIZON-style).

When the workspace is a git repository, a run can execute in an isolated *worktree* —
a separate checkout on a throwaway branch — so the agent's edits never touch the main
checkout until they are verified. On success only the files the agent actually changed
are copied back (so a user's other uncommitted work is preserved); either way the
worktree is removed. Outside a git repo this is a no-op (the run uses the workspace
directly), so callers can always opt in safely.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

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


#: Every isolated worktree's directory name starts with this, wherever it lives. It is what makes a
#: directory ours to count and to collect; nothing without it is ever touched.
WORKTREE_PREFIX = "chimera-wt-"

#: Written into the worktree's own git admin directory (`.git/worktrees/<name>/`), never into the
#: checkout: a file in the checkout would be one of the run's "changes" and be copied back into the
#: person's project. It says which process made the worktree, which is the only way a different
#: process — the storage card, a later run — can tell a killed run's leftover from a run that is
#: still working.
OWNER_FILE = "chimera-owner.json"

#: Worktrees this process made and has not removed yet. Checked before anything else, so a run in
#: this process is never collected, whatever its age and whatever the owner file says.
_live_here: set[Path] = set()


def _configured_worktree_dir() -> Path | None:
    """``CHIMERA_WORKTREE_DIR`` as an absolute path; None when it is empty or not absolute."""
    from chimera.config import get_settings

    configured = (get_settings().worktree_dir or "").strip()
    if not configured:
        return None
    folder = Path(configured).expanduser()
    return folder if folder.is_absolute() else None


def is_inside(folder: Path, root: Path) -> bool:
    """Whether ``folder`` is ``root`` or lies under it, compared resolved. False when unknowable."""
    try:
        return Path(folder).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def worktree_parent(repo_root: Path | None = None) -> Path:
    """Where the next worktree made from ``repo_root`` goes: ``CHIMERA_WORKTREE_DIR``, or temp.

    Pure — it decides, it never creates. It used to make the configured folder too, and so every
    READ that asked where worktrees go (`GET /api/storage`, `GET /api/diagnostics`, `chimera doctor`,
    the boot-time prune) created a directory — inside the owner's project, when that is where the
    setting pointed. Only :func:`_make_worktree_parent`, on the way to an actual worktree, creates.

    The configured folder is passed over for temp when it is not an absolute path, when it lies
    inside ``repo_root`` (that repository's status, search and checkpoints would all start reading
    the run's checkout), or when something that is not a folder is in its place. Every caller that
    REPORTS this must pass the same ``repo_root`` the run will: asked without it, this answered the
    configured folder while `create` put the worktree in temp, and the card said one thing while the
    code did another.
    """
    temp = Path(tempfile.gettempdir())
    folder = _configured_worktree_dir()
    if folder is None:
        return temp
    if repo_root is not None and is_inside(folder, repo_root):
        return temp
    with suppress(OSError):
        if folder.exists() and not folder.is_dir():
            return temp
    return folder


def _make_worktree_parent(repo_root: Path) -> Path:
    """:func:`worktree_parent`, created — the one place that may make the folder, and warns when the
    setting is passed over. Refusing would fail the run over a setting; ignoring silently would leave
    the owner believing their disk was being spared. The warning is the middle."""
    from chimera.config import get_settings

    temp = Path(tempfile.gettempdir())
    configured = (get_settings().worktree_dir or "").strip()
    parent = worktree_parent(repo_root)
    if configured and parent == temp:
        _log.warning(
            "CHIMERA_WORKTREE_DIR=%r is not absolute, is inside the project %s, or is not a folder;"
            " using %s", configured, repo_root, temp,
        )
        return temp
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.warning("CHIMERA_WORKTREE_DIR=%s cannot be created (%s); using %s", parent, exc, temp)
        return temp
    return parent


def worktree_parents() -> list[Path]:
    """Every folder a worktree may have been made in: temp, and the configured one if any.

    Both, always: changing the setting does not move the worktrees already made, and a leftover in
    the old place is still a leftover. The configured folder is listed whatever project it lies in —
    a run in another repository may have used it — and a folder that does not exist holds nothing.
    """
    parents = [Path(tempfile.gettempdir())]
    configured = _configured_worktree_dir()
    if configured is not None and configured not in parents:
        parents.append(configured)
    return parents


def find_worktree_dirs() -> list[Path]:
    """Every ``chimera-wt-*`` directory in the folders a worktree may have been made in."""
    found: list[Path] = []
    for parent in worktree_parents():
        with suppress(OSError):
            found.extend(p for p in parent.glob(f"{WORKTREE_PREFIX}*") if p.is_dir())
    return sorted(set(found))


def _read_admin_dir(path: Path) -> Path | None:
    """The worktree's git admin directory, read from its ``.git`` file; None when it has none.

    A linked worktree's ``.git`` is a FILE holding ``gitdir: <repo>/.git/worktrees/<name>``. A
    directory without one is not a worktree at all — what is left when git forgot it, or when
    `create` died between making the folder and registering it.

    Raises ``OSError`` when the file is there but could not be read. That is not "has none": an
    antivirus or indexer holding the file for a moment on Windows, or the repository's drive gone
    for a second, used to come back as None — "unregistered" — and an hour-old checkout of a run
    still working was then removed by the next prune. Only a missing file is an answer.
    """
    marker = path / ".git"
    try:
        info = os.stat(marker)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    line = marker.read_text(encoding="utf-8", errors="replace").strip()
    if not line.startswith("gitdir:"):
        return None
    admin = Path(line[len("gitdir:") :].strip())
    if not admin.is_absolute():
        admin = (path / admin).resolve()
    return admin


def _admin_dir(path: Path) -> Path | None:
    """:func:`_read_admin_dir` for the callers that act only on a known admin directory (recording
    the owner, unregistering a dead run): unreadable is as good as absent to them. Never use this to
    DECIDE that a worktree is unregistered — :func:`classify_worktree_dir` does not."""
    try:
        return _read_admin_dir(path)
    except OSError:
        return None


AdminPresence = Literal["present", "forgotten", "unreachable"]


def _admin_presence(admin: Path) -> AdminPresence:
    """Whether git still knows the worktree whose ``.git`` file points at ``admin``.

    ``forgotten`` only when the repository's git directory is there and the admin entry is not —
    git pruned it. When the git directory itself cannot be reached (a drive unmounted for a moment,
    a network share, a path this account may not read) nothing is known, and that is
    ``unreachable``, which keeps the worktree. Also kept by this: the leftover of a repository the
    owner deleted outright. Disk, against a live checkout read as dead.
    """
    try:
        return "present" if stat.S_ISDIR(os.stat(admin).st_mode) else "unreachable"
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError:
        return "unreachable"
    if admin.parent.name != "worktrees":
        return "unreachable"
    try:
        common_is_dir = stat.S_ISDIR(os.stat(admin.parent.parent).st_mode)
    except OSError:
        return "unreachable"
    return "forgotten" if common_is_dir else "unreachable"


#: Two readings of one process's start time, both from the OS, agree to well within this. Wider than
#: the float rounding psutil does on its own, and far narrower than any pid reuse could be.
_SAME_START_SECONDS = 2.0


def process_identity(pid: int) -> dict[str, Any]:
    """What identifies the process ``pid`` beyond its number: its start time AS THE OS REPORTS IT,
    its executable and its command line. Whatever cannot be read is left out, never guessed."""
    try:
        import psutil
    except ImportError:
        return {}
    identity: dict[str, Any] = {}
    try:
        proc = psutil.Process(pid)
        identity["started"] = float(proc.create_time())
    except (psutil.Error, OSError):
        return identity
    with suppress(psutil.Error, OSError):
        identity["exe"] = proc.exe()
    with suppress(psutil.Error, OSError):
        identity["cmdline"] = list(proc.cmdline())
    return identity


def _write_owner(path: Path) -> None:
    """Record which process made this worktree. Best-effort: a run is not refused for this, and a
    worktree without the record is simply never collected by anything but its own `remove`.

    ``created`` is the wall clock and is kept for a person reading the file; it is never compared
    with anything. Liveness is judged on ``started``, this process's start time read from the same
    source the check will read it from later — see :func:`_owner_state`.
    """
    admin = _admin_dir(path)
    if admin is None:
        return
    record = {"pid": os.getpid(), "created": time.time(), **process_identity(os.getpid())}
    with suppress(OSError):
        (admin / OWNER_FILE).write_text(json.dumps(record), encoding="utf-8")


OwnerState = Literal["running", "gone", "unknown", "uncertain"]


def _owner_state(owner: dict[str, Any]) -> OwnerState:
    """Whether the process recorded in ``owner`` is still the one running under its pid.

    ``gone`` is the only answer that lets a worktree be collected, so it is given only on evidence:
    no process has the pid, or the process that has it is visibly a different program (another
    executable or command line). It used to be decided by comparing the process's start time from
    psutil with the WALL CLOCK at the moment the worktree was made, within one second — two clocks.
    A wall clock that stepped back after the backend started (a w32time correction, a dual-boot
    machine whose RTC is in UTC, a resumed VM) made a live maker look like a reused pid, and the
    prune deleted a working run's checkout. Reproduced in review with the record five seconds off.

    Now both sides are psutil's ``create_time``. On Windows that is the kernel's fixed creation
    stamp and does not move; on Linux/WSL it is boot time plus ticks and moves WITH a clock jump, so
    a mismatch alone is not proof either — it is ``uncertain`` unless the program differs.

    Not ``os.kill(pid, 0)``: on Windows signal 0 is CTRL_C_EVENT, and that call would interrupt the
    process it was asking about. A process we may not inspect is counted as running: the cost of
    keeping a dead run's checkout is disk, the cost of the opposite is someone's work.
    """
    try:
        import psutil
    except ImportError:
        return "unknown"
    try:
        proc = psutil.Process(int(owner["pid"]))
        started = float(proc.create_time())
    except psutil.NoSuchProcess:
        return "gone"
    except (psutil.Error, OSError):
        return "running"
    recorded = owner.get("started")
    if isinstance(recorded, int | float) and abs(started - float(recorded)) <= _SAME_START_SECONDS:
        return "running"
    try:
        if "exe" in owner and os.path.normcase(proc.exe()) != os.path.normcase(str(owner["exe"])):
            return "gone"
        if "cmdline" in owner and list(proc.cmdline()) != list(owner["cmdline"]):
            return "gone"
    except psutil.NoSuchProcess:
        return "gone"
    except (psutil.Error, OSError, TypeError):
        return "uncertain"
    return "uncertain"


WorktreeState = Literal["live", "orphan", "kept"]


@dataclass(frozen=True)
class WorktreeDir:
    """What is known about one ``chimera-wt-*`` directory, and whether it may be collected.

    ``live`` and ``kept`` are never touched; only ``orphan`` is. ``kept`` is the honest third
    answer — a worktree whose maker cannot be identified (made by a version that recorded no
    owner, on a machine where processes cannot be inspected, or whose pid runs the same program
    with a start time that disagrees with the record) is not called dead on a guess.
    ``reason`` is a fixed word, so a screen can translate it.
    """

    path: Path
    state: WorktreeState
    reason: str
    registered: bool


def classify_worktree_dir(path: Path, *, now: float | None = None) -> WorktreeDir:
    """Decide whether a worktree directory belongs to a run that is still working."""
    path = Path(path)
    with suppress(OSError):
        if path.resolve() in _live_here:
            return WorktreeDir(path, "live", "this_process", True)
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except OSError:
        return WorktreeDir(path, "kept", "unreadable", False)
    if age < _ORPHAN_MIN_AGE_SECONDS:
        return WorktreeDir(path, "live", "too_new", False)
    try:
        admin = _read_admin_dir(path)
    except OSError:
        return WorktreeDir(path, "kept", "unreadable", False)
    if admin is None:
        return WorktreeDir(path, "orphan", "unregistered", False)
    presence = _admin_presence(admin)
    if presence == "forgotten":
        return WorktreeDir(path, "orphan", "unregistered", False)
    if presence == "unreachable":
        return WorktreeDir(path, "kept", "unreachable", True)
    try:
        owner = json.loads((admin / OWNER_FILE).read_text(encoding="utf-8"))
        if not isinstance(owner, dict):
            raise TypeError("owner record is not an object")
        int(owner["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return WorktreeDir(path, "kept", "no_owner", True)
    owner_state = _owner_state(owner)
    if owner_state == "running":
        return WorktreeDir(path, "live", "owner_running", True)
    if owner_state == "gone":
        return WorktreeDir(path, "orphan", "owner_gone", True)
    # `unknown`: processes cannot be inspected here. `uncertain`: the pid runs the same program but
    # its start time disagrees with the record — a clock jump or a reuse, and nothing says which.
    return WorktreeDir(path, "kept", f"owner_{owner_state}", True)


def _remove_registered(path: Path, admin: Path) -> None:
    """Unregister a dead run's worktree from its repository, and delete its attempt branch.

    Through the repository's own git directory rather than its checkout, which this code does not
    otherwise know: the admin directory names its common directory, and that is all git needs.
    Only a branch named like ours is deleted — the HEAD of a worktree someone repurposed by hand is
    not ours to remove.
    """
    common = admin.parent.parent
    with suppress(OSError):
        pointer = (admin / "commondir").read_text(encoding="utf-8").strip()
        if pointer:
            common = (admin / pointer).resolve()
    branch = ""
    with suppress(OSError):
        head = (admin / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: refs/heads/"):
            branch = head[len("ref: refs/heads/") :]
    git_dir = [f"--git-dir={common}"]
    _git([*git_dir, "worktree", "remove", "--force", str(path)], common)
    if "/attempt-" in branch:
        _git([*git_dir, "branch", "-D", branch], common)
    _git([*git_dir, "worktree", "prune"], common)


def prune_worktree_dirs() -> dict[str, int]:
    """Collect every orphaned worktree directory, in every repository. The storage card's action.

    Each directory is classified again immediately before it is touched, so the answer acted on is
    never older than the action. Live and kept directories are counted and left; the result says
    how many of each, and how many bytes the removed ones held.
    """
    from chimera.core.storage import tree_size

    result = {"removed": 0, "bytes_freed": 0, "kept": 0, "live": 0, "failed": 0}
    for candidate in find_worktree_dirs():
        state = classify_worktree_dir(candidate)
        if state.state != "orphan":
            result["live" if state.state == "live" else "kept"] += 1
            continue
        size = tree_size(candidate)
        admin = _admin_dir(candidate)
        if state.registered and admin is not None:
            _remove_registered(candidate, admin)
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
        if candidate.exists():
            result["failed"] += 1
            continue
        result["removed"] += 1
        result["bytes_freed"] += size.bytes or 0
    if result["removed"]:
        _log.info("pruned orphaned worktree directories: %s", result)
    return result


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

    # 3. Worktree directories NO repository knows about. Age-gated: `create` makes the directory
    #    and registers it a moment later, and pruning inside that window would delete a live
    #    worktree belonging to another process.
    #
    #    "No repository", not "not this one". This step used to keep only the directories in THIS
    #    repository's worktree list, and every `chimera-wt-*` lives in one shared temp folder — so
    #    the first run in repository A deleted, from under it, the worktree of a run that had been
    #    working in repository B for more than an hour. A directory registered with any repository
    #    is left to `prune_worktree_dirs`, which asks whether the process that made it is alive.
    for candidate in find_worktree_dirs():
        state = classify_worktree_dir(candidate)
        if state.state != "orphan" or state.registered:
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        if not candidate.exists():
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
        path = Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX, dir=_make_worktree_parent(repo_root)))
        # Counted as live from before git knows it: the age gate covers other processes in that
        # window, and this covers this one for as long as the run lasts, however long that is.
        _live_here.add(path.resolve())
        path.rmdir()  # `git worktree add` needs the target not to exist yet
        result = _git(["worktree", "add", "-b", branch, str(path), "HEAD"], repo_root)
        if result.returncode != 0:
            _live_here.discard(path.resolve())
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")
        _write_owner(path)
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

    def diff_stat(self) -> tuple[str, int]:
        """The staged unified diff against HEAD, and how many lines it adds plus removes.

        Read with the same ``_IGNORE_DIRS`` filter as :meth:`changed_paths`, so bytecode the verify
        command wrote is neither shown to a person as part of a solution nor counted as its size.
        A binary file has no line count (git prints ``-``) and is counted as one line: it is a
        change, and counting it as zero would make a worker that only rewrote a binary look like
        the smallest edit in the crew.

        The filter is passed to git as exclude pathspecs rather than as the list of kept paths: a
        worker that touched thousands of files would otherwise put all of them on one command line,
        which Windows caps at 32k characters.
        """
        self.changed_paths()  # stages untracked files, so they appear in the diff below
        spec = ["--", ".", *(f":(exclude,glob)**/{name}/**" for name in sorted(_IGNORE_DIRS))]
        lines = 0
        numstat = _git(["diff", "--cached", "--numstat", "HEAD", *spec], self.path)
        for row in numstat.stdout.splitlines():
            added, _, rest = row.partition("\t")
            removed, _, path = rest.partition("\t")
            if not path or any(part in _IGNORE_DIRS for part in Path(path).parts):
                continue
            if added == "-" or removed == "-":
                lines += 1
                continue
            with suppress(ValueError):
                lines += int(added) + int(removed)
        patch = _git(["diff", "--cached", "HEAD", *spec], self.path)
        return patch.stdout, lines

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
        _live_here.discard(self.path.resolve())


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
