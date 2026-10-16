"""A coding conversation's state in the list, and whether it may be archived — from facts only.

Six states, each a fact the server holds and none a guess (no model is called, ever):

* ``blocked`` — a question this conversation asked is waiting for the owner (`GET /api/approvals`
  lists the same question, and its ``run_id`` is a turn of THIS conversation);
* ``running`` — a turn of it is running, or a background work it started is queued or running;
* ``failed`` — its last turn raised, or the edits it made failed the workspace's test command;
* ``done`` — the owner said so (``POST /api/code/sessions/{id}/done``), and no turn ended since;
* ``review`` — its last turn changed files the owner's screen has not shown since, or a pull request
  was opened from it — by the agent's tool or from the owner's Git panel, one record for both;
* ``idle`` — none of the above.

Precedence, first match wins: **blocked, running, failed, done, review, idle**.

``blocked`` outranks ``running`` although a question only exists while its turn runs. Read in the
other order, every blocked conversation would be "running" and the filter for the conversations
that need you would always be empty — the one state the list exists to surface would be unreachable.

``failed`` outranks ``done`` only in a way that cannot happen through the app: a turn ending clears
``done``, so a failure after it is new news. ``done`` outranks ``review`` because a recorded PR
never goes away: read the other way, a conversation that opened a PR could never be called done,
and the owner's own word would be the one fact the list ignores. Whether a PR was merged is not
read here — that is a network call (C1's PR status), and a list built per request must not make one
per row.

A turn that ended without failing and without editing is ``idle``, which claims nothing about
whether the work is finished: only the owner says ``done``.
"""

from __future__ import annotations

from typing import Literal

from chimera.core.code_session_marks import ConversationMark

State = Literal["blocked", "review", "done", "running", "failed", "idle"]

#: Seconds in a day, for ``CHIMERA_ARCHIVE_AFTER_DAYS``.
DAY = 86_400.0


def conversation_state(
    *, running: bool, waiting: bool, mark: ConversationMark, last_verdict: str = ""
) -> State:
    """The state one row of the list shows. See the module docstring for the order."""
    if waiting:
        return "blocked"
    if running:
        return "running"
    # A conversation whose turns all ended before the marks file existed still has its last
    # receipt, and the receipt keeps the verifier's verdict. An error then left no record, so an
    # old conversation can read "failed" only for a failing test command — never for less than that.
    failed = mark.last_failed if mark.turns_ended else last_verdict == "failed"
    if failed:
        return "failed"
    if mark.completed:
        return "done"
    if mark.unseen_diff or mark.pull_requests:
        return "review"
    return "idle"


def archive_refusal(*, running: bool, waiting: bool, background: bool, shared: bool) -> str:
    """Why the AUTOMATIC rule must leave a conversation in the list, or ``""`` when nothing holds it.

    Each is something an archived conversation would hide while it still matters: work in progress,
    a question that refuses itself if nobody answers it, a work running on its behalf, and a person
    holding a link to it who would see a conversation the owner no longer sees.
    """
    if running:
        return "a turn is running in it"
    if waiting:
        return "a question in it is waiting for you"
    if background:
        return "a background work of it has not finished"
    if shared:
        return "it is shared through a link"
    return ""


def due_for_archive(
    *, updated_at: float, mark: ConversationMark, now: float, after_days: float | None
) -> bool:
    """Whether a conversation has been left alone longer than ``after_days``.

    Inactivity counts from the later of its last write and the last time it was brought back from
    the archive: unarchiving writes nothing to the transcript, so measuring from the write alone
    would send a conversation someone just brought back straight back again.
    """
    if not after_days or after_days <= 0 or mark.archived_at is not None:
        return False
    last_activity = max(updated_at, mark.unarchived_at or 0.0)
    return now - last_activity > after_days * DAY
