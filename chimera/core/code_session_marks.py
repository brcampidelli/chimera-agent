"""What the conversation list knows about each coding conversation besides its transcript.

The sidebar used to say one thing about a conversation: whether a turn was running in it. A person
with several conversations open could not tell, without opening each one, which had stopped to ask
them something, which had failed, and which had changed files they had not looked at yet. Each of
those is a FACT the server already holds for a moment — a question file, a verifier's verdict, an
edit list — and loses when the turn ends. This file keeps the part that has to outlive the turn:

* how many turns of the conversation have ended, and how the last one ended (failed, edited);
* the turn count the person had seen when their screen last showed the conversation;
* when it was archived, and when it was last brought back.
* the pull requests opened from it — by the agent's tool or the owner's Git panel — with the owner's
  two options for each, and whether the owner called it done;
* what it is called, when that is not its first message: the owner's rename, or a title stamped at
  a turn (`chimera/core/conversation_title.py`).

Nothing here is judged by a model. "Failed" is the workspace's own test command failing or the turn
raising, "edited" is the agent's own edit list, "seen" is the desktop saying it drew the
conversation. A state a model inferred would be a state that is sometimes wrong in a list whose
whole job is to be believed at a glance.

**Kept beside the transcripts, never inside them**, and that is load-bearing twice over. The list
orders conversations by the file's modification time, so writing "seen" into the transcript when a
person opens it would move every conversation they read to the top. And ``CodeSessionStore.save``
writes the file from the session object, which does not carry these fields, so the next turn's save
would erase an archive. One small JSON file per store, read on every call rather than cached: the
CLI and the app can both write it, and a cache is a second source of truth that drifts.

**The one record of a conversation's pull requests.** Two writers used to keep two records: the
Git panel wrote its PR into the transcript (C1, `pull_request`) and the agent's tool wrote here (C3),
so a PR opened from the panel never counted in the sidebar and one the agent opened never showed on
the status card. Both write here now. This file, and not the transcript, because the list already
reads it on every call: a count derived from the transcripts would either reopen every conversation
file per list (what #923 removed) or ride a cache whose key is the transcript's mtime — and writing
the PR into the transcript is a write that reorders the list. An older transcript that still carries
C1's field is read as a second, legacy source and merged (:func:`merge_pull_requests`) until the
first write moves it here.

**Archiving touches nothing.** It is a timestamp in this file. The transcript, the folder, any
worktree and any share link are exactly as they were, which is what makes it safe to do
automatically and to undo with one click.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from chimera.core.conversation_title import clean_title
from chimera.core.filelock import atomic_write_text
from chimera.core.redact import redact
from chimera.telemetry import get_logger

_log = get_logger("core.code_session_marks")

#: The owner's switches for a conversation's pull request, both OFF until the owner turns them on.
PULL_REQUEST_OPTIONS = ("fix_on_ci_failure", "cleanup_on_merge")

#: Why a conversation was archived, kept on its record so the archived row can say it: ``owner`` (the
#: person's own archive), ``inactive`` (``CHIMERA_ARCHIVE_AFTER_DAYS``), ``pr_merged`` / ``pr_closed``
#: (``CHIMERA_ARCHIVE_ON_PR_MERGE``, `chimera/core/pr_auto_archive.py`), ``worktree_pruned`` (the
#: worktree of the conversation was removed by the project's prune).
ARCHIVE_REASONS = ("owner", "inactive", "pr_merged", "pr_closed", "worktree_pruned")

#: The last state read for a conversation's latest pull request. ``open`` is written when the PR is
#: recorded; a later read that finds it merged or closed is the change the automatic archive acts on.
PR_STATES = ("open", "merged", "closed")


def archive_reason_for_pr(*, pr_state: str, state: str) -> str:
    """Why a conversation leaves the list because its PR is now ``state``, given the state recorded for
    it, or ``""``. Only a change from a recorded ``open`` counts: a state met for the first time, or one
    already archived for, never archives. Decided where the record is written (under the lock), so a
    snapshot read earlier cannot make it act on a state that has since changed."""
    if pr_state != "open":
        return ""
    if state == "merged":
        return "pr_merged"
    if state == "closed":
        return "pr_closed"
    return ""


def pull_request_record(raw: object) -> dict[str, Any] | None:
    """A stored pull request record, validated, or None when there is none or it is unusable.

    Read from files an older or newer version may have written, so nothing in it is trusted: the
    number must be a positive int (a bool is an int in Python and is refused — `True` is not PR #1),
    the URL a string, and each option is ON only when stored as exactly ``True`` — anything else,
    including a record from before the options existed, reads as the default, off.
    """
    if not isinstance(raw, dict):
        return None
    number = raw.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    url = raw.get("url")
    record: dict[str, Any] = {"number": number, "url": url[:2048] if isinstance(url, str) else ""}
    for option in PULL_REQUEST_OPTIONS:
        record[option] = raw.get(option) is True
    return record


def _same_pull_request(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The URL is a PR's identity (the same number in another repository is another PR); a record
    without one — C1 allowed an empty URL — falls back to the number."""
    if a["url"] and b["url"]:
        return bool(a["url"] == b["url"])
    return bool(a["number"] == b["number"])


def merge_pull_requests(
    recorded: list[dict[str, Any]], legacy: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """This file's PRs plus a transcript's C1 record that is not already among them.

    Neither old format kept a time, so the legacy record goes in by NUMBER: one conversation works in
    one repository, where GitHub numbers rise, and "the latest PR" is the card's question. When this
    file already has the same PR, its copy wins — the options were last changed here.
    """
    out = [dict(item) for item in recorded]
    if legacy is None or any(_same_pull_request(item, legacy) for item in out):
        return out
    at = next((i for i, item in enumerate(out) if item["number"] > legacy["number"]), len(out))
    out.insert(at, dict(legacy))
    return out


#: How often `opened` writes: the removal clock is in days, so an hour is as fine as it needs.
OPENED_RESOLUTION_SECONDS = 3600.0


@dataclass
class ConversationMark:
    """One conversation's facts. The defaults are what a conversation with no entry has."""

    #: When it was archived, or None while it is in the list.
    archived_at: float | None = None
    #: Why it was archived (:data:`ARCHIVE_REASONS`), kept after it is brought back so the archived
    #: row can say why before the person brings it back. "" when it never was.
    archive_reason: str = ""
    #: When it was last brought back from the archive. The automatic rule measures inactivity from
    #: the later of this and the last write, or a conversation someone just unarchived would go
    #: straight back on the next look, because unarchiving writes nothing to the transcript.
    unarchived_at: float | None = None
    #: Turns of this conversation that ended since this file existed. A counter rather than the
    #: transcript's turn count, because the transcript trims its oldest turns and a count that can
    #: go down cannot say "a turn ended after you looked".
    turns_ended: int = 0
    #: ``turns_ended`` as it was when the desktop last drew this conversation.
    last_seen_turn: int = 0
    #: The last turn ended in an error, or its edits failed the workspace's test command.
    last_failed: bool = False
    #: The last turn edited files.
    last_edited: bool = False
    last_ended_at: float | None = None
    #: Pull requests opened from this conversation — by the agent's tool or the owner's panel —
    #: oldest first, each ``{"number", "url", "fix_on_ci_failure", "cleanup_on_merge"}``
    #: (:func:`pull_request_record`). A list, because one conversation can open more than one; the
    #: URL is the identity, so a retried call that reports the same PR again is not counted twice.
    #: The options belong to each PR: switching them on for one is not consent for the next.
    pull_requests: list[dict[str, Any]] = field(default_factory=list)
    #: The state the pull-request sweep last read for the latest PR (:data:`PR_STATES`), "" when none
    #: was read. Recorded ``open`` when the PR is recorded, so a merge the sweep finds later is a
    #: change it saw, not a state it met for the first time (`chimera/core/pr_auto_archive.py`).
    pr_state: str = ""
    #: The owner said this conversation is done. Only the owner's own route sets it, and the next
    #: turn that ends clears it: new work after "done" is not done. Whether a PR was MERGED is not
    #: read here — that is a network question, and a list that guessed it would be believed.
    completed: bool = False
    #: The owner's name for this conversation; "" means the automatic one. Only the owner's rename
    #: writes it, and it outranks every other source (`chimera/core/conversation_title.py`).
    title: str = ""
    #: A title written at a turn, not by the owner: the one the caller passed with the turn, or the
    #: heading of the task file its first message named. Here and not in the transcript, so a
    #: compaction that drops the first message cannot take the title with it.
    stamped_title: str = ""
    #: Where :attr:`stamped_title` came from: ``caller`` or ``heading`` ("" when there is none).
    stamped_title_source: str = ""
    #: The last time the person's screen drew this conversation, to the hour (`opened`). With the
    #: archive time it sets when the conversation's worktree may be removed (`worktree_expiry.py`).
    opened_at: float | None = None
    #: Why the last removal sweep kept this conversation's worktree ("" when it removed it, or never
    #: looked): a refusal code from `worktree_prune`. Shown on the archived row, so it says what to do.
    worktree_kept: str = ""
    #: When the sweep last looked at this worktree; a kept one is looked at again once a day.
    worktree_checked_at: float | None = None
    #: When the sweep removed this conversation's worktree. Set once; nothing to show after it.
    worktree_removed_at: float | None = None

    @property
    def pull_request(self) -> dict[str, Any] | None:
        """The latest pull request, the one the status card shows; None when there is none."""
        return dict(self.pull_requests[-1]) if self.pull_requests else None

    @property
    def unseen_diff(self) -> bool:
        """The last turn changed files and the person has not looked since it ended."""
        return self.last_edited and self.turns_ended > self.last_seen_turn


class CodeSessionMarks:
    """The marks of one store's conversations, in one JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- reading ---------------------------------------------------------------------------------

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # An unreadable file costs the badges and the archive, never the conversations: they are
            # in their own files. Starting empty is right; refusing to list would not be.
            _log.warning("conversation marks unreadable, read as empty: %s", exc)
            return {}
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(sessions, dict):
            return {}
        return {str(k): v for k, v in sessions.items() if isinstance(v, dict)}

    @staticmethod
    def _mark(entry: dict[str, Any] | None) -> ConversationMark:
        known = {f.name for f in fields(ConversationMark)}
        try:
            mark = ConversationMark(**{k: v for k, v in (entry or {}).items() if k in known})
        except TypeError:
            return ConversationMark()
        # The file is written by this and other versions of the app: a record that is not a
        # positive int number and a string URL is dropped, never shown as a PR, and an entry from
        # before the options lived here (C3 wrote only number and URL) reads with both options off.
        raw = mark.pull_requests if isinstance(mark.pull_requests, list) else []
        mark.pull_requests = [
            record
            for record in (
                pull_request_record(item)
                for item in raw
                if isinstance(item, dict) and isinstance(item.get("url"), str)
            )
            if record is not None
        ]
        mark.completed = mark.completed is True
        mark.archive_reason = mark.archive_reason if mark.archive_reason in ARCHIVE_REASONS else ""
        mark.pr_state = mark.pr_state if mark.pr_state in PR_STATES else ""
        # Titles are read back from a file other versions write too: a non-string is no title, and
        # one longer than the cap is cut rather than drawn whole into a row.
        mark.title = clean_title(mark.title) if isinstance(mark.title, str) else ""
        mark.stamped_title = (
            clean_title(mark.stamped_title) if isinstance(mark.stamped_title, str) else ""
        )
        if mark.stamped_title_source not in ("caller", "heading") or not mark.stamped_title:
            mark.stamped_title, mark.stamped_title_source = "", ""
        return mark

    def get(self, session_id: str) -> ConversationMark:
        return self._mark(self._read().get(session_id))

    def all(self) -> dict[str, ConversationMark]:
        return {sid: self._mark(entry) for sid, entry in self._read().items()}

    # -- writing ---------------------------------------------------------------------------------

    def _update(self, session_id: str, change: Any) -> ConversationMark:
        """Read, change one entry, write — under the lock, so two turns ending at once both count."""
        with self._lock:
            sessions = self._read()
            mark = self._mark(sessions.get(session_id))
            change(mark)
            sessions[session_id] = asdict(mark)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps({"sessions": sessions}))
            return mark

    def turn_ended(
        self,
        session_id: str,
        *,
        failed: bool,
        edited: bool,
        at: float,
    ) -> None:
        def change(mark: ConversationMark) -> None:
            mark.turns_ended += 1
            mark.last_failed = failed
            mark.last_edited = edited
            mark.last_ended_at = at
            # A turn ended after the owner called the conversation done: it is not done any more.
            mark.completed = False

        self._update(session_id, change)

    def seen(self, session_id: str) -> bool:
        """The person's screen drew this conversation. Returns whether that changed anything, so a
        screen that reports every draw writes the file only when there was something unseen."""
        if not self.get(session_id).unseen_diff:
            return False

        def change(mark: ConversationMark) -> None:
            mark.last_seen_turn = mark.turns_ended

        self._update(session_id, change)
        return True

    def holds(self, session_id: str, predicate: Callable[[ConversationMark], bool]) -> bool:
        """Whether ``predicate`` is true of this conversation's record AS IT IS, read under the same
        lock the writers take: a check that must not be answered from an older snapshot."""
        with self._lock:
            return bool(predicate(self._mark(self._read().get(session_id))))

    def opened(self, session_id: str, *, at: float) -> None:
        """The person's screen drew this conversation. Written at most once an hour: the removal
        clock runs in days, so an hour of precision costs nothing and a screen that reports every
        draw does not write the file on every one."""
        current = self.get(session_id).opened_at
        if current is not None and at - current < OPENED_RESOLUTION_SECONDS:
            return

        def change(mark: ConversationMark) -> None:
            mark.opened_at = at

        self._update(session_id, change)

    def worktree_verdict(
        self,
        session_id: str,
        *,
        at: float,
        kept: str = "",
        removed: bool = False,
    ) -> ConversationMark:
        """Record what the removal sweep found for this conversation's worktree."""

        def change(mark: ConversationMark) -> None:
            mark.worktree_checked_at = at
            mark.worktree_kept = kept
            if removed:
                mark.worktree_removed_at = at

        return self._update(session_id, change)

    def pull_request_opened(
        self,
        session_id: str,
        *,
        number: int,
        url: str,
        legacy: dict[str, Any] | None = None,
    ) -> ConversationMark:
        """Record a PR opened from this conversation, by the agent or the owner's panel.

        The same PR twice counts once and keeps the owner's options; a new one goes last with both
        options off. ``legacy`` is the transcript's C1 record, written here in the same step so the
        old record and the new one cannot end up in two places.
        """
        record = pull_request_record({"number": number, "url": url})
        if record is None:
            raise ValueError(f"not a pull request number: {number!r}")

        def change(mark: ConversationMark) -> None:
            mark.pull_requests = merge_pull_requests(mark.pull_requests, legacy)
            if not any(_same_pull_request(item, record) for item in mark.pull_requests):
                mark.pull_requests.append(record)
                mark.pr_state = "open"

        return self._update(session_id, change)

    def settle_pull_request(
        self,
        session_id: str,
        *,
        number: int,
        url: str,
        state: str,
        at: float,
        free: Callable[[str], bool],
    ) -> str:
        """Record what the sweep read for a conversation's latest PR, and archive it when the read is a
        settled change — all under this store's lock, after revalidating what the read was about.

        The read was made earlier, outside the lock, so before anything is written the latest PR must
        still be the one that was read (same number, and the same URL when one is recorded), and the
        conversation must still be free — ``free`` is asked now, not at the start of the pass. Returns
        ``archived``, ``recorded``, ``url_recorded``, ``unchanged``, ``held`` or ``moved``. ``moved`` means
        the PR the read was about is no longer the conversation's latest, and nothing is written.

        ``free`` runs UNDER the lock on purpose: it reads live turns, pending questions and share links,
        none of which takes this lock, so it cannot deadlock, and checking it outside would leave a window
        between the check and the write in which a turn could start. The residual window that remains is
        the one after the write: a turn that starts then finds the conversation archived and brings it back
        (the same rule as any turn in an archived conversation), so it is shown, not lost.

        A record with no URL (C1 allowed one) is identified by its number. The first read that carries the
        URL writes it and archives nothing in that pass (``url_recorded``); the next pass compares URLs.
        """
        if state not in PR_STATES:
            raise ValueError(f"not a pull request state: {state!r}")
        with self._lock:
            sessions = self._read()
            mark = self._mark(sessions.get(session_id))
            latest = mark.pull_request
            if latest is None or latest["number"] != number or (latest["url"] and latest["url"] != url):
                return "moved"
            if not latest["url"]:
                if not url:
                    return "unchanged"
                mark.pull_requests[-1]["url"] = url
                sessions[session_id] = asdict(mark)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(self.path, json.dumps({"sessions": sessions}))
                return "url_recorded"
            reason = archive_reason_for_pr(pr_state=mark.pr_state, state=state)
            if reason:
                if mark.archived_at is not None:
                    return "unchanged"
                if not free(session_id):
                    return "held"
                mark.archived_at = at
                mark.archive_reason = reason
                mark.pr_state = state
                outcome = "archived"
            elif state != mark.pr_state:
                mark.pr_state = state
                outcome = "recorded"
            else:
                return "unchanged"
            sessions[session_id] = asdict(mark)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps({"sessions": sessions}))
            return outcome

    def set_pr_state(self, session_id: str, state: str) -> None:
        """Record the state the sweep read for the latest PR (:data:`PR_STATES`)."""
        if state not in PR_STATES:
            raise ValueError(f"not a pull request state: {state!r}")

        def change(mark: ConversationMark) -> None:
            mark.pr_state = state

        self._update(session_id, change)

    def set_pull_request_options(
        self,
        session_id: str,
        options: dict[str, bool],
        *,
        legacy: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Change the owner's options on the conversation's LATEST PR; None, and nothing written,
        when it has none. Unknown keys are ignored, and only an exact ``True`` turns one on."""
        if not merge_pull_requests(self.get(session_id).pull_requests, legacy):
            return None

        def change(mark: ConversationMark) -> None:
            mark.pull_requests = merge_pull_requests(mark.pull_requests, legacy)
            for option in PULL_REQUEST_OPTIONS:
                if option in options:
                    mark.pull_requests[-1][option] = options[option] is True

        return self._update(session_id, change).pull_request

    def adopt_pull_request(self, session_id: str, legacy: dict[str, Any]) -> None:
        """Move a transcript's C1 record here, before the transcript is written without it."""
        current = self.get(session_id).pull_requests
        if merge_pull_requests(current, legacy) == current:
            return  # already here: nothing to write

        def change(mark: ConversationMark) -> None:
            mark.pull_requests = merge_pull_requests(mark.pull_requests, legacy)

        self._update(session_id, change)

    def set_completed(self, session_id: str, *, completed: bool) -> ConversationMark:
        """The owner marked the conversation done (or took it back). Only the app's owner route
        calls this; the bridge has no route to it (``tests/test_a_conversation_says_what_it_needs_from_facts.py``)."""

        def change(mark: ConversationMark) -> None:
            mark.completed = completed

        return self._update(session_id, change)

    def rename(self, session_id: str, title: str) -> ConversationMark:
        """The owner names the conversation; an empty (or all-blank) title gives it back its
        automatic one. Cleaned the same way every title is."""
        cleaned = clean_title(redact(title))

        def change(mark: ConversationMark) -> None:
            mark.title = cleaned

        return self._update(session_id, change)

    def stamp_title(self, session_id: str, title: str, *, source: str) -> ConversationMark | None:
        """Record a title written at a turn (``source`` ``caller`` or ``heading``); None, and
        nothing written, when it cleans to nothing or says what is already there."""
        if source not in ("caller", "heading"):
            raise ValueError(f"not a title source: {source!r}")
        cleaned = clean_title(redact(title))
        current = self.get(session_id)
        if not cleaned or (cleaned, source) == (
            current.stamped_title,
            current.stamped_title_source,
        ):
            return None

        def change(mark: ConversationMark) -> None:
            mark.stamped_title = cleaned
            mark.stamped_title_source = source

        return self._update(session_id, change)

    def copy_titles(self, source_id: str, target_id: str) -> None:
        """Give a fork the titles of the conversation it was copied from — what it is called is
        part of the past the two share. Nothing else of the marks goes: the PRs, the archive and the
        turn counters belong to the conversation that has them."""
        source = self.get(source_id)
        if not (source.title or source.stamped_title):
            return

        def change(mark: ConversationMark) -> None:
            mark.title = source.title
            mark.stamped_title = source.stamped_title
            mark.stamped_title_source = source.stamped_title_source

        self._update(target_id, change)

    def archive(
        self,
        session_id: str,
        *,
        at: float,
        reason: str = "owner",
        pr_state: str | None = None,
    ) -> ConversationMark:
        """Move a conversation out of the list, recording why. ``pr_state`` is written in the same
        step when the pull-request sweep archives on a change it read — so the record of the state
        and the archive cannot disagree after a crash between two writes."""
        if reason not in ARCHIVE_REASONS:
            raise ValueError(f"not an archive reason: {reason!r}")
        if pr_state is not None and pr_state not in PR_STATES:
            raise ValueError(f"not a pull request state: {pr_state!r}")

        def change(mark: ConversationMark) -> None:
            mark.archived_at = at
            mark.archive_reason = reason
            if pr_state is not None:
                mark.pr_state = pr_state

        return self._update(session_id, change)

    def unarchive(self, session_id: str, *, at: float) -> ConversationMark:
        def change(mark: ConversationMark) -> None:
            mark.archived_at = None
            mark.unarchived_at = at

        return self._update(session_id, change)

    def forget(self, session_id: str) -> None:
        """Drop one conversation's marks — what deleting the conversation must also do, so nothing
        about a conversation outlives it here."""
        with self._lock:
            sessions = self._read()
            if sessions.pop(session_id, None) is None:
                return
            atomic_write_text(self.path, json.dumps({"sessions": sessions}))
