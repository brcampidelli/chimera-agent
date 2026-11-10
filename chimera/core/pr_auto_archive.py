"""Archive a coding conversation when its pull request is merged or closed — the owner's switch.

``CHIMERA_ARCHIVE_ON_PR_MERGE``, ON by default, the Desktop's "auto-archive after merge or close".
Archiving is a timestamp beside the transcripts (`code_session_marks.py`): the folder, the worktree,
the branch and any share link are untouched, and bringing the conversation back is one click.

The state of a pull request is a network read (``gh pr view``), so it cannot be asked once per row of
the list. Three pieces keep it off the list's path:

* :func:`sweep` reads the latest PR of each conversation whose PR is not yet settled, and archives the
  ones whose PR it SEES change from ``open`` to ``merged`` or ``closed``. A PR is recorded ``open`` the
  moment it is opened. A merged PR is final and is never read again. A closed PR can be reopened, so it
  is re-read after the unsettled conversations, with what the per-pass bound has left.
* :class:`Sweeper` runs :func:`sweep` on a daemon thread, at most once per :data:`SWEEP_SECONDS`, from
  the list's look. When GitHub cannot be asked at all (not signed in, offline, the CLI missing) the pass
  stops and the sweeper waits :data:`BACKOFF_SECONDS` before trying again. An error about one PR (its
  folder is gone, the PR was deleted) waits only that conversation.
* The write is :meth:`CodeSessionMarks.settle_pull_request`: under the marks lock it re-checks that the
  PR read is still the conversation's latest, that the conversation is still free, and only then writes.

A conversation a turn is running in, a question of it waits on, a background work of it has not
finished, or a share link opens is left alone — the same four holds the other automatic rule uses
(`conversation_state.archive_refusal`). Held is asked per conversation at the moment of the write. A held
conversation is not recorded as settled, so a later sweep, once it is free, still sees the change.
Nothing is removed: the worktree and the branch belong to the cleanup (`pull_request_cleanup.py`).

"Pinned" is not a hold here: a conversation cannot be pinned in Chimera (projects and models can).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.code_session_marks import (
    PR_STATES,
    CodeSessionMarks,
    ConversationMark,
    archive_reason_for_pr,
)

#: At most this often a sweep reads GitHub. The list is polled, so the throttle is what keeps the
#: switch from costing a request per poll.
SWEEP_SECONDS = 60.0
#: How long the sweeper waits after GitHub could not answer before it tries again.
BACKOFF_SECONDS = 600.0
#: How long the background thread waits before its first read. The list that kicked it is still
#: answering, and the sweep's own reads (the marks, the pending questions, `gh`) must not land inside
#: that answer: a poll's cost is fixed by the list alone (`test_the_state_adds_a_constant_number_of_
#: reads_...`), and a thread that starts reading at once makes it depend on thread timing.
START_DELAY_SECONDS = 2.0
#: Bounds the reads one pass makes. Settled conversations are skipped before they count.
MAX_PER_SWEEP = 25


@dataclass(frozen=True)
class PrReading:
    """What one read of a PR said. ``failed`` means GitHub could not answer: the pass stops there."""

    state: str = ""
    url: str = ""
    failed: bool = False


class SweepBlocked(Exception):
    """GitHub could not answer a read. The pass stops, and the sweeper backs off."""


def pull_request_archive_reason(*, pr_state: str, state: str) -> str:
    """See :func:`chimera.core.code_session_marks.archive_reason_for_pr`."""
    return archive_reason_for_pr(pr_state=pr_state, state=state)


def _same_url(recorded: str, read: str) -> bool:
    """The PR's identity. With a recorded URL, GitHub must report the same one. With none (C1 allowed
    one), the number already matched is all there is, and the read URL is written by the settle step
    without archiving anything (`settle_pull_request` returns ``url_recorded``)."""
    if not recorded:
        return True
    return bool(read) and recorded.rstrip("/") == read.rstrip("/")


def sweep(
    marks: CodeSessionMarks,
    *,
    read: Callable[[Path, int], PrReading],
    folder_of: Callable[[str], Path],
    held: Callable[[str], bool],
    now: float,
    snapshot: Mapping[str, ConversationMark] | None = None,
) -> list[str]:
    """One pass: read the latest PR of each candidate conversation and settle the ones that changed.

    Candidates are ordered: unsettled first (open, or never read), then closed. A merged PR is final and
    is never read again; a closed PR can be reopened, so it is re-read, but only with what the bound has
    left after the unsettled ones, so closed conversations cannot starve open ones.

    A reading that is an error about one PR (deleted, folder gone, a generic failure) skips that
    conversation and nothing else. A reading that says GitHub cannot be asked at all raises
    :class:`SweepBlocked`, and the pass stops there. Returns the ids archived.
    """
    # The candidates come from the snapshot the list read (or one read here when none is given). Each
    # write re-reads its own record under the lock, so a stale snapshot can only skip a conversation for
    # one pass, never act on a state that has since changed.
    candidates: list[tuple[str, ConversationMark, dict[str, Any]]] = []
    source = marks.all() if snapshot is None else snapshot
    for sid, mark in source.items():
        pr = mark.pull_request
        if mark.archived_at is None and pr is not None:
            candidates.append((sid, mark, pr))
    unsettled = [c for c in candidates if c[1].pr_state not in ("merged", "closed")]
    closed = [c for c in candidates if c[1].pr_state == "closed"]

    archived: list[str] = []
    checked = 0
    for sid, _mark, pr in [*unsettled, *closed]:
        if checked >= MAX_PER_SWEEP:
            break
        checked += 1
        number, url = int(pr["number"]), str(pr["url"])
        reading = read(folder_of(sid), number)
        if reading.failed:
            raise SweepBlocked("GitHub could not answer a pull request read")
        if reading.state not in PR_STATES:
            continue  # an error about this PR: this conversation waits, the others do not
        if not _same_url(url, reading.url) or (not url and not reading.url):
            continue
        outcome = marks.settle_pull_request(
            sid,
            number=number,
            url=url or reading.url,  # a record with no URL takes the one GitHub reports
            state=reading.state,
            at=now,
            free=lambda session_id: not held(session_id),
        )
        if outcome == "archived":
            archived.append(sid)
    return archived


class Sweeper:
    """Runs :func:`sweep` in the background: at most once per interval, never two passes at once, and
    after a :class:`SweepBlocked` no pass at all until the backoff has passed. :meth:`kick` returns at
    once; it is called from the list, which must not wait on GitHub."""

    def __init__(
        self,
        interval: float = SWEEP_SECONDS,
        *,
        backoff: float = BACKOFF_SECONDS,
        delay: float = START_DELAY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = interval
        self.backoff = backoff
        self.delay = delay
        self._clock = clock
        self._lock = threading.Lock()
        self._running = False
        self._last = float("-inf")
        self._retry_at = float("-inf")

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def kick(self, run: Callable[[], object], *, enabled: bool) -> bool:
        """Start one pass in the background when the switch is on, the interval has passed and no
        backoff is in force. Returns whether a pass was started. With the switch off nothing starts and
        the clock does not move, so turning it on later sweeps at once."""
        if not enabled:
            return False
        at = self._clock()
        with self._lock:
            if self._running or at < self._retry_at or at - self._last < self.interval:
                return False
            self._running = True
            self._last = at

        def work() -> None:
            try:
                if self.delay > 0:
                    time.sleep(self.delay)
                run()
            except SweepBlocked:
                with self._lock:
                    self._retry_at = self._clock() + self.backoff
            finally:
                with self._lock:
                    self._running = False

        try:
            threading.Thread(target=work, name="pr-auto-archive", daemon=True).start()
        except RuntimeError:
            with self._lock:
                self._running = False
            return False
        return True
