"""Remove the worktree of an archived conversation once its time is up — the owner's switch.

``CHIMERA_WORKTREE_CLEANUP``, ON by default. A conversation's worktree stays on disk when the
conversation is archived (archiving touches no folder). This module removes it about thirty days
after the latest of three moments: the archiving, the last time the owner's screen drew the
conversation (``ConversationMark.opened_at``), and the day the switch was first seen on
(:class:`ActivationClock`) — so a conversation archived before this version existed is not due on the
first sweep. Unarchiving before then keeps it: an archived-and-back conversation is not archived, so
nothing here looks at it.

Removal is not reversible, so the rules are narrower than the clock:

* the removal is :func:`chimera.core.worktree_prune.prune_worktrees` with ``dry_run=False`` — the same
  refusals the owner's cleanup dialog applies: a dirty tree, a commit not pushed, a branch merged
  nowhere, a worktree Chimera did not make, the main checkout, a locked or detached one. A refused
  worktree is kept, and the refusal's code is written on the conversation's record, where the archived
  row reads it. Nothing is forced, and nothing is pushed;
* the unattended removal also keeps a worktree holding ANY ignored file that `.worktreeinclude` did
  not copy (``ignored_files``): a ``.env.local`` or a database made in the conversation is not in
  ``git status``, and nobody is looking when this runs. The owner's dialog does not apply that
  refusal: the owner sees the list and picks;
* the record is read again under its lock right before the removal, and a conversation that was
  brought back, opened or moved on in between is left alone;
* the FOLDER is protected, not only the conversation: another conversation that uses the same folder
  and is not archived-and-due (or has a turn, work or question) keeps it;
* never ``--force``, never ``git branch -D``, never a push: a worktree with uncommitted changes,
  unpushed commits or an ignored file nobody copied is kept, and the branch of a worktree with no
  upstream is left in place, so what goes is recoverable from the remote branch;
* a conversation a turn runs in, a question waits on, background work is in, or a share link opens is
  left alone for this pass and asked again on the next one (``held``);
* a folder that is missing while the folder it lives in is missing too (a drive that is off) is kept
  as ``unavailable``, not closed out as removed;
* only a folder named like a conversation worktree is looked at (``chimera-wt-``): the rest of the
  project's worktrees are the owner's and the dialog's, not this clock's.

The sweep runs on a background thread from the conversation list's look (`code_api.py`), and it reads
its candidates from the snapshot the list already took, so the list itself reads no file and runs no
git. A kept worktree is looked at again once a day, not every sweep.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from chimera.core.code_session_marks import CodeSessionMarks, ConversationMark
from chimera.core.filelock import atomic_write_text
from chimera.core.worktree import WORKTREE_PREFIX
from chimera.core.worktree_prune import PruneResult, prune_worktrees
from chimera.telemetry import get_logger

_log = get_logger("core.worktree_expiry")

#: Days after the latest of archiving, last look and activation before the worktree may go.
RETENTION_DAYS = 30
RETENTION_SECONDS = RETENTION_DAYS * 86400.0

#: A kept worktree is looked at again after this long, not on every sweep.
RECHECK_SECONDS = 86400.0

#: Kept when the conversation's folder is not a git repository any more.
NOT_A_REPOSITORY = "not_a_repository"

#: Kept when the folder is missing and so is the folder it lives in (a drive that is off, a share
#: that is not mounted): "not there now" is not "removed".
UNAVAILABLE = "unavailable"

ACTIVATION_FILE = "worktree_cleanup_since.json"

Pruner = Callable[..., "list[PruneResult] | None"]


class ActivationClock:
    """The day the removal switch was first seen on.

    A conversation archived before this feature existed carries an old ``archived_at``; without a floor
    the first sweep would find them all due and erase months of worktrees at once. The floor is written
    once and never moves. It is read when the app starts (:meth:`load`, never in a route's window), set
    in memory the first time the switch is seen on, and written by :meth:`persist` on the sweep's own
    thread. A file that cannot be written leaves the floor in memory, which is safe: the worst a
    restart does is start the thirty days again."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._value: float | None = None
        self._dirty = False

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            value: float | None = float(raw["activated_at"])
        except (OSError, ValueError, KeyError, TypeError):
            value = None
        with self._lock:
            if value is not None and self._value is None:
                self._value = value

    def value(self, *, enabled: bool) -> float | None:
        """The floor, or None while the switch has never been on."""
        if not enabled:
            return None
        with self._lock:
            if self._value is None:
                self._value = self._clock()
                self._dirty = True
            return self._value

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._dirty

    def persist(self) -> None:
        with self._lock:
            if not self._dirty or self._value is None:
                return
            value = self._value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps({"activated_at": value}))
        except OSError as exc:
            _log.warning("could not record when worktree cleanup was switched on: %s", exc)
            return
        with self._lock:
            self._dirty = False


def removal_due_at(mark: ConversationMark, *, activated_at: float | None = None) -> float | None:
    """When this conversation's worktree may go; None when it is not archived or already removed.
    The latest of the archiving, the last look and the day the switch was first on, plus thirty days."""
    if mark.archived_at is None or mark.worktree_removed_at is not None:
        return None
    return max(mark.archived_at, mark.opened_at or 0.0, activated_at or 0.0) + RETENTION_SECONDS


def is_conversation_worktree(workspace: str) -> bool:
    """Whether the folder is named the way a conversation's worktree is. A name check only: the
    sweep verifies ownership itself, before it removes anything."""
    return bool(workspace) and Path(workspace).name.startswith(WORKTREE_PREFIX)


def removal_view(
    mark: ConversationMark,
    *,
    workspace: str,
    enabled: bool,
    activated_at: float | None = None,
) -> dict[str, object] | None:
    """What an archived row shows about its worktree: when it goes and what kept it so far. None
    when there is nothing to say — the switch is off, the conversation is not archived, or its
    folder is not a conversation worktree (the main checkout of a project, for one)."""
    if not enabled or not is_conversation_worktree(workspace):
        return None
    due = removal_due_at(mark, activated_at=activated_at)
    if due is None:
        return None
    return {"due_at": due, "kept": mark.worktree_kept}


def _due_now(mark: ConversationMark, *, now: float, activated_at: float | None) -> bool:
    due = removal_due_at(mark, activated_at=activated_at)
    return due is not None and due <= now


def _is_candidate(mark: ConversationMark, *, now: float, activated_at: float | None) -> bool:
    if not _due_now(mark, now=now, activated_at=activated_at):
        return False
    # A kept worktree is looked at once a day: until then there is no work, and no thread.
    return not (mark.worktree_checked_at is not None and now - mark.worktree_checked_at < RECHECK_SECONDS)


def has_expiry_work(
    snapshot: Mapping[str, ConversationMark], *, now: float, activated_at: float | None = None
) -> bool:
    """Whether any archived conversation is due and not looked at today, decided in memory: a list
    with none starts no thread and reads nothing."""
    return any(_is_candidate(m, now=now, activated_at=activated_at) for m in snapshot.values())


def candidates(
    snapshot: Mapping[str, ConversationMark],
    *,
    folder_of: Callable[[str], Path | None],
    now: float,
    activated_at: float | None = None,
) -> list[tuple[str, Path]]:
    """The archived conversations whose time is up, with their worktree folder, in a stable order."""
    out: list[tuple[str, Path]] = []
    for sid in sorted(snapshot):
        if not _is_candidate(snapshot[sid], now=now, activated_at=activated_at):
            continue
        folder = folder_of(sid)
        if folder is None or not is_conversation_worktree(str(folder)):
            continue
        out.append((sid, folder))
    return out


def _gone_for_good(folder: Path) -> bool:
    """The folder is missing AND the folder it lives in (or that one's parent) is there: removed by
    hand or by the dialog. With both missing it is a drive that is off, not a removal."""
    return folder.parent.is_dir() or folder.parent.parent.is_dir()


def sweep_expired(
    marks: CodeSessionMarks,
    *,
    snapshot: Mapping[str, ConversationMark],
    folder_of: Callable[[str], Path | None],
    held: Callable[[str], bool],
    now: float,
    prune: Pruner = prune_worktrees,
    activated_at: float | None = None,
    folder_users: Callable[[Path], Sequence[str]] | None = None,
) -> list[str]:
    """One pass. Returns the ids whose worktree was removed. Every outcome that is decided is written
    to the conversation's record under its lock; a held conversation is written nothing and asked
    again later."""
    removed: list[str] = []

    def still_due(sid: str) -> bool:
        # The record as it is NOW, under its lock. The snapshot is from the list's look: a turn or the
        # owner may have brought the conversation back since (``archived_at`` cleared), or opened it
        # (the clock moved). Asked again at the last moment before the removal, as the refusals are.
        return marks.holds(sid, lambda m: _due_now(m, now=now, activated_at=activated_at))

    def folder_in_use(sid: str, folder: Path) -> bool:
        # Every OTHER conversation that has this folder as its workspace must itself be archived,
        # due and free; one that is not keeps the folder (two conversations can share one).
        for other in folder_users(folder) if folder_users is not None else ():
            if other == sid:
                continue
            if held(other) or not marks.holds(
                other, lambda m: _due_now(m, now=now, activated_at=activated_at)
            ):
                return True
        return False

    for sid, folder in candidates(snapshot, folder_of=folder_of, now=now, activated_at=activated_at):
        if held(sid) or not still_due(sid):
            continue
        if folder_in_use(sid, folder):
            continue
        if not folder.is_dir():
            if _gone_for_good(folder):
                marks.worktree_verdict(sid, at=now, removed=True)
            else:
                marks.worktree_verdict(sid, at=now, kept=UNAVAILABLE)
            continue
        results = prune(
            folder,
            [str(folder)],
            dry_run=False,
            busy=lambda _p, s=sid, f=folder: held(s) or not still_due(s) or folder_in_use(s, f),
            protect_ignored=True,
        )
        if results is None:
            marks.worktree_verdict(sid, at=now, kept=NOT_A_REPOSITORY)
            continue
        result = results[0]
        if result.removed:
            marks.worktree_verdict(sid, at=now, removed=True)
            removed.append(sid)
            _log.info("removed the worktree of archived conversation %s", sid)
        else:
            marks.worktree_verdict(sid, at=now, kept=result.kept or "unknown")
    return removed
