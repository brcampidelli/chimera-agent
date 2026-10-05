"""Append-only audit log (JSONL) for governance decisions and evolution changes.

Entries are **hash-chained**: each one carries the digest of the entry before it, and its own digest
over that. Append-only was previously a convention — the file was ordered by a ``seq`` counter and
nothing else, so anyone who could edit the file could rewrite history and leave no trace. A chain
turns that into a detectable event: changing, reordering, or deleting an entry that still has a
successor breaks the link from that point on, and :meth:`AuditLog.verify` says exactly where.

What this does and does not buy you, stated plainly:

- It **detects** tampering of a log you still hold. It does not **prevent** it.
- Deleting the NEWEST entries breaks nothing. This docstring used to say "deleting any entry breaks
  every digest from that point on", and for the tail that was false: drop the last three lines and
  every surviving link still holds. Reproduced: six entries cut to three, ``verify()`` answered
  ``ok=True, checked=3`` — and the newest entries are exactly the ones an incident would make
  somebody delete. The chain alone cannot see a missing END, only a missing middle.
- An attacker who can rewrite the whole file can also recompute the whole chain. The chain raises the
  bar from "edit one line" to "forge every entry after it".
- Both gaps close only against a record of the head kept OUTSIDE the log. :func:`anchor_of` gives the
  ``(count, head)`` pair; it is written onto every run receipt (``runs.jsonl``) and every cron result
  line, and ``verify(anchor=...)`` reports *truncated* or *rewritten since the anchor*. Those files
  sit in the same home directory, so this raises the bar to "edit three files consistently"; it is
  not an anchor on a host the attacker does not control, and nothing here pretends otherwise.
- Entries written before chaining existed carry no digest. :meth:`verify` reports them as *unchained*
  rather than as tampered — an honest "cannot say", not a false pass. That allowance covers a PREFIX
  only: an unchained entry after a chained one is a break, or stripping ``hash`` from an edited entry
  would turn tampering into "legacy".

A chain also has to survive **two writers**, and it did not. The head digest and the entry count
were read once per :class:`AuditLog` and advanced only in memory, so two instances over one file
each believed they were alone. Measured, four alternating appends::

    entries=4 seqs=[0, 0, 1, 1]
    verify -> ok=False broken_at=1 reason='broken link to previous entry'

Which is the worse half of the failure: a log nobody had touched reported itself as tampered, on a
screen whose whole job is to say whether it was. A chain that cries wolf is not a weaker guarantee
than a missing one, it is the same lost trust arriving from the other side. So every append now
re-reads the head from disk while holding a lock — see :meth:`AuditLog.record` for which writers
that does and does not cover.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.filelock import locked

_log = logging.getLogger(__name__)

# Reserved on every entry; a payload may not override them, or the chain would be forgeable by the
# caller that is supposed to be audited.
_CHAIN_KEYS = ("prev", "hash")

GENESIS = "0" * 64
"""``prev`` of the first entry — a fixed anchor, so entry 0 is chained like any other."""


def _digest(entry: dict[str, Any]) -> str:
    """SHA-256 over the entry's canonical JSON, excluding its own ``hash`` field.

    Canonical = sorted keys and no incidental whitespace, so the digest depends on the *content*
    rather than on how json happened to serialise it.
    """
    body = {k: v for k, v in entry.items() if k != "hash"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainCheck:
    """Result of walking the chain. ``ok`` is False only for a link that is actually broken."""

    ok: bool
    checked: int
    """Entries whose digest was verified."""
    unchained: int
    """Legacy entries with no digest — cannot be verified either way."""
    broken_at: int | None
    """Index of the first entry whose digest or link does not hold."""
    reason: str

    def __bool__(self) -> bool:
        return self.ok


def _redacted(payload: dict[str, Any]) -> dict[str, Any]:
    """Run every string in ``payload`` through the trace's redactor before it is written.

    `chimera.core.redact` existed and was wired into the step trace only. This file is the one that
    gets SERVED — `/api/governance/audit` reads it straight onto the Security screen — and it was
    the one with no redaction at all. Measured, with a governed write of a `.env`:

        LINHAS CONTENDO A CHAVE LITERAL: 1
        write_file {'path': '.env', 'content': 'OPENAI_API_KEY=sk-AAAABBBBCCCCDDDD1234\n'}

    Note *which* rule wrote that line: `secret_material`, the rule whose entire job is to notice a
    credential. It noticed, and then persisted it.

    Applied here rather than at each caller so the guarantee covers the whole file, and applied
    BEFORE the hash so the digest is over what is actually stored. It is a second net and not a
    promise — see the redactor's own docstring for what it cannot catch, which is why
    :func:`chimera.governance.governed_tool.elide_values` drops the argument bodies as well.
    """
    from chimera.core.redact import redact

    return {
        key: redact(value) if isinstance(value, str) else value for key, value in payload.items()
    }


#: How much of the file's tail to pull per read while looking for the final line. Comfortably
#: larger than any entry written here — the kernel truncates ``action`` and ``reason`` to 200
#: characters — so the loop below finds its newline on the first read in the ordinary case.
_TAIL_CHUNK = 8192

#: How many times `record` re-tries when the FILE lock could not be taken.
#:
#: `locked()` degrades rather than raising: on Windows `msvcrt.locking` retries for ~10s and then
#: gives up, and the helper writes unlocked so a 24/7 process is never wedged. That is the right
#: default for most callers and the wrong one here — two processes that both skip the lock read the
#: same tail, claim the same `seq`, and chain onto the same `prev`. Caught as an intermittent CI
#: failure: `seq duplicado: [0, 1, 2, 1, 2, 3, ...]`.
#:
#: Three attempts turn a ~10s window into ~30s of trying. It cannot close the hole — without a lock
#: there is no way to make read-then-append atomic — but it makes losing the race require sustained
#: contention rather than a moment of it.
_LOCK_ATTEMPTS = 3

#: Seconds between attempts, multiplied by the attempt number.
_LOCK_BACKOFF_S = 0.25

#: One lock per audit file per process, shared by every :class:`AuditLog` naming that file. Bounded
#: by how many distinct audit files a process touches, never by how much it writes to them.
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _process_lock(path: Path) -> threading.Lock:
    """The lock every :class:`AuditLog` naming ``path`` in this process shares.

    Keyed by the normalised absolute path, which is pure string work: this sits on the append path
    and ``Path.resolve()`` would add a syscall to every record. Two spellings that only the
    filesystem can tell apart — a symlink — fall through to the file lock, which the OS keys by the
    real file rather than by the name used to reach it.

    A plain lock rather than a reentrant one, deliberately. Nothing reachable from ``record()``
    records, and if something ever did, the file lock underneath would block on its own second
    handle regardless — so an ``RLock`` here would buy the appearance of reentrancy without it.
    """
    key = os.path.normcase(os.path.abspath(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PATH_LOCKS[key] = threading.Lock()
    return lock


def _line_count(path: Path) -> int:
    """Non-blank lines — what ``seq`` counts. Zero when absent, and never raises on garbage.

    The fallback for the two cases the tail cannot answer, so it deliberately parses nothing: a
    file whose last line was torn by a crash must still accept new entries. Reading it through
    :meth:`AuditLog.entries` would raise on that line and turn one bad append into a log that
    refuses every append after it.
    """
    try:
        with path.open("rb") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _last_entry(path: Path) -> dict[str, Any] | None:
    """Parse the newest entry by reading the END of the file, never the whole of it.

    An append needs exactly two things from what is already on disk: the newest ``seq`` and the
    newest ``hash``. Parsing the whole file to find them would make every append cost more than the
    one before it, on a file that only ever grows under a host that runs 24/7. Measured, 200 appends
    onto a file already holding M lines, microseconds per record:

        M            0     500    2000     8000    20000
        tail      62.6    62.4    65.9     65.3     63.9
        whole    168.7   714.8  2318.4  11380.3  34606.6

    Flat against quadratic, 542x apart by twenty thousand entries — a size this log reaches on its
    own. That gap is the whole reason the head is read from the end of the file instead.

    ``None`` means the tail cannot answer: absent, empty, or a final line that will not parse.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            buffer = b""
            while position > 0:
                step = min(_TAIL_CHUNK, position)
                position -= step
                handle.seek(position)
                buffer = handle.read(step) + buffer
                trimmed = buffer.rstrip()  # the trailing newline, and any blank lines after it
                cut = trimmed.rfind(b"\n")
                if cut == -1 and position:
                    continue  # the line straddles the chunk boundary — take another bite
                parsed = json.loads(trimmed[cut + 1 :]) if trimmed else None
                return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError):
        return None
    return None


@dataclass(frozen=True)
class AuditAnchor:
    """The log's size and newest digest at one moment, kept somewhere other than the log."""

    count: int
    """Entries the log held, i.e. the ``seq`` the next entry would get."""
    head: str
    """``hash`` of the newest entry at that moment."""

    def fields(self) -> dict[str, Any]:
        """The two keys a receipt or a cron result line carries."""
        return {"audit_count": self.count, "audit_head": self.head}

    @classmethod
    def from_record(cls, record: Any) -> AuditAnchor | None:
        """Read an anchor back off a receipt or result line; ``None`` when it carries none.

        Defensive on purpose: these lines come from files that predate the fields, and a line
        that does not hold a well-formed anchor is a line with no anchor, never a crash.
        """
        if not isinstance(record, dict):
            return None
        count, head = record.get("audit_count"), record.get("audit_head")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return None
        if not isinstance(head, str) or len(head) != len(GENESIS) or head == GENESIS:
            return None
        return cls(count, head)


def anchor_of(path: Path) -> AuditAnchor | None:
    """The anchor for the log at ``path`` as it stands on disk, read from its tail only.

    ``None`` when there is nothing to vouch for: no file, an empty one, or a newest line that is torn
    or carries no digest. An anchor to GENESIS would assert "this log was empty", which is a claim
    the next legitimate append contradicts at once.

    Cheap enough for the paths that call it — one per run receipt, one per cron result — because it
    reads the end of the file the way :meth:`AuditLog.record` does, never the whole of it.
    """
    last = _last_entry(Path(path))
    if last is None:
        return None
    head = last.get("hash")
    if not isinstance(head, str) or not head:
        return None
    seq = last.get("seq")
    count = seq + 1 if isinstance(seq, int) and not isinstance(seq, bool) else _line_count(Path(path))
    return AuditAnchor(count, head)


#: How much of each anchor-bearing file's END to read. A receipt embeds diffs and runs to tens of
#: KB, so this is sized to hold several of the newest; an anchor older than the window is not read.
ANCHOR_TAIL_BYTES = 512 * 1024


def recorded_anchors(home: Path) -> list[AuditAnchor]:
    """Every anchor in the tail of ``runs.jsonl`` and ``scheduler/cron_results.jsonl``, strongest first.

    ALL of them, not the newest line of each file. Only the newest was read, and the newest is
    written by the next ordinary append (a cron tick, a finished run) from the log as it stands on
    disk. Cut the log after an anchor, wait one tick, and that tick anchored the cut log: the
    Security screen went from "truncated" back to "ok" with nobody touching either anchor file.
    Measured on six entries cut to three: ``ok=False``, one ``deliver()``, ``ok=True, checked=3``.
    The log only grows, so EVERY head ever anchored must still be in it; checking all of them is
    what keeps an earlier anchor from being outvoted by a later one.

    Only the tail of each file is read: both grow for the life of the install. Empty when neither
    carries an anchor, which leaves the check exactly as it was before anchors existed.
    """
    from chimera.scheduler.results import _tail_lines

    found: dict[AuditAnchor, None] = {}  # ordered and de-duplicated: cron lines repeat an anchor
    for source in (Path(home) / "runs.jsonl", Path(home) / "scheduler" / "cron_results.jsonl"):
        try:
            lines = _tail_lines(source, ANCHOR_TAIL_BYTES)
        except OSError:
            continue
        for line in lines:
            try:
                anchor = AuditAnchor.from_record(json.loads(line))
            except ValueError:
                continue
            if anchor is not None:
                found[anchor] = None
    return sorted(found, key=lambda anchor: anchor.count, reverse=True)


#: How every chained line ends: ``record()`` writes ``hash`` last, through json.dumps' default
#: separators, so the head is the 64 characters before the closing quote and brace.
_HEAD_MARK = b'"hash": "'
_HEAD_LINE_TAIL = len(_HEAD_MARK) + len(GENESIS) + len(b'"}')


def _heads_on_disk(path: Path) -> set[str]:
    """The ``hash`` of every line in the log, read off the END of each line without parsing it.

    The anchor writers call this on every cron tick and every receipt, over a file that only grows;
    a byte comparison per line keeps that a scan rather than a parse. A nested ``"hash"`` inside a
    payload cannot be mistaken for the entry's own: the entry's own is the last key on the line. A
    line written some other way contributes no head, which can only make the writer repeat an
    older anchor, never invent one.
    """
    heads: set[str] = set()
    mark_start = -_HEAD_LINE_TAIL
    mark_end = mark_start + len(_HEAD_MARK)
    try:
        with Path(path).open("rb") as handle:
            for raw in handle:
                line = raw.rstrip()
                if len(line) < _HEAD_LINE_TAIL or not line.endswith(b'"}'):
                    continue
                if line[mark_start:mark_end] == _HEAD_MARK:
                    heads.add(line[mark_end:-2].decode("ascii", "replace"))
    except OSError:
        return set()
    return heads


def anchor_to_record(log_path: Path, home: Path) -> AuditAnchor | None:
    """The anchor a receipt or a cron line should carry now, never one that blesses a cut.

    :func:`anchor_of` alone answers "what does the log look like now", and writing that answer
    after somebody cut the log is the log vouching for its own truncation. So the anchors already
    recorded under ``home`` are checked first: if any of their heads is gone from the log, THAT
    anchor is written again instead of a new one. The break then rides forward on every later tick
    rather than lasting until the next one.

    Recorded anchors are read BEFORE the log. The log only grows, so whatever another writer
    anchored before this read is in the file by the time it is scanned; in the other order an
    anchor written in between could name a head this scan never saw, and an honest log would be
    reported as cut.

    Costs one byte scan of the log per call (:func:`_heads_on_disk`): a call per cron tick and per
    run receipt, never per audit append.
    """
    priors = recorded_anchors(home)
    if priors:
        heads = _heads_on_disk(log_path)
        for prior in priors:  # strongest first: the anchor that says the most about what was lost
            if prior.head not in heads:
                return prior
    return anchor_of(log_path)


class AuditLog:
    """An append-only, hash-chained JSONL audit trail."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        entries = self.entries()
        self._count = len(entries)
        # Resume the chain from whatever is already on disk, so appending to an existing log keeps
        # one continuous chain instead of silently starting a second one. Both fields are a cache
        # for `head` and `len()` to answer from before anything is written; `record()` re-reads them
        # from disk under a lock rather than trusting what this snapshot said.
        last = entries[-1] if entries else None
        self._head = str(last.get("hash", "")) if last else GENESIS
        if not self._head:  # legacy tail with no digest — chain restarts, and verify() will say so
            self._head = GENESIS

    def _tail_state(self) -> tuple[int, str]:
        """``(seq, prev)`` for the next entry, taken from DISK rather than from this instance."""
        last = _last_entry(self.path)
        if last is None:
            # Absent, empty, or a final line torn by a crash. Counting the lines still gives an
            # honest `seq`, and the chain restarts from GENESIS — the same answer this file has
            # always given for a gap it cannot span, which `verify()` reports as unchained rather
            # than as tampering.
            return _line_count(self.path), GENESIS
        seq = last.get("seq")
        # `record()` writes `seq` back over a payload's own, but lines written before it did may
        # carry a payload's "seq", or none. Counting the lines is the honest answer then.
        if isinstance(seq, int) and not isinstance(seq, bool):
            count = seq + 1
        else:
            count = _line_count(self.path)
        head = last.get("hash")
        return count, head if isinstance(head, str) and head else GENESIS

    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Two locks, always in this order, so there is one ordering and no cycle to deadlock on.
        #
        # The process lock is not redundant with the file one. Measured on Windows, a second handle
        # on the same lock file from the SAME process blocks for 9.1s and then raises `[Errno 36]
        # Resource deadlock avoided`, at which point `locked()` takes its degraded path and writes
        # unlocked — dropping the guarantee in precisely the case that dominates here, because
        # `assemble_registry` builds an AuditLog per request and the API serves each request on its
        # own thread. Holding the process lock first leaves the file lock arbitrating only BETWEEN
        # processes, which is the job it is good at and the one `chimera serve` needs: the cron
        # daemon and the HTTP gateway write this same file from different processes, all day.
        # Retry rather than accept the degraded write. `locked()` yields whether a real lock was
        # taken precisely so a caller can react, and this is the caller that must: an unlocked
        # append duplicates `seq` and breaks the chain, which the Security screen then reports as
        # tampering on a log nobody touched.
        #
        # The last attempt writes ANYWAY. An audit log that silently drops entries under contention
        # is worse than one with a break somebody can see and explain — a missing entry is never
        # honest about itself, and a break at least is.
        #
        # Marking the entry and letting `verify` treat it as unchained was the other candidate and
        # is worse: `unlocked: true` would become a field anyone can forge to make the chain restart
        # wherever they want it to, which widens the one hatch the legacy path already opens.
        for attempt in range(_LOCK_ATTEMPTS):
            with _process_lock(self.path), locked(self.path) as got_lock:
                if not got_lock and attempt < _LOCK_ATTEMPTS - 1:
                    continue
                if not got_lock:
                    _log.error(
                        "appending to %s WITHOUT the file lock after %d attempts; a concurrent "
                        "writer can duplicate seq and break the chain here",
                        self.path.name,
                        _LOCK_ATTEMPTS,
                    )
                seq, prev = self._tail_state()
                entry: dict[str, Any] = {"seq": seq, "type": event_type, **_redacted(payload)}
                # `seq` is put back after the payload: a payload carrying its own "seq" replaced it,
                # and the anchors count entries from the newest `seq`. The key keeps its first
                # position, so the line reads the same; only a forged value is undone.
                entry["seq"] = seq
                # Chain fields are written last on purpose: a payload cannot overwrite them.
                entry["prev"] = prev
                entry["hash"] = _digest(entry)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._count = seq + 1
                self._head = entry["hash"]
                return entry
            time.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        raise AssertionError("unreachable: the last attempt always writes")

    def entries(self) -> list[dict[str, Any]]:
        """Every entry that parses, in order.

        Per line, not per file. This was a list comprehension around a bare `json.loads`, so one
        truncated line — the ordinary outcome of a crash during an append — made the whole log
        unreadable, including the entries written BEFORE the crash, which are the ones an incident
        needs. A line that does not parse is one entry lost, and `verify()` will report the chain
        break at that point, which is the honest answer.
        """
        if not self.path.exists():
            return []
        saida: list[dict[str, Any]] = []
        for numero, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                saida.append(json.loads(line))
            except ValueError:
                _log.warning("unreadable audit line %d in %s", numero, self.path)
        return saida

    def verify(
        self,
        entries: list[dict[str, Any]] | None = None,
        *,
        anchor: AuditAnchor | None = None,
        anchors: Sequence[AuditAnchor] = (),
    ) -> ChainCheck:
        """Walk the chain and report the first break.

        A legacy entry (no ``hash``) BEFORE the first chained one is counted as *unchained* and
        skipped rather than failed — the log cannot vouch for what predates the chain, and saying so
        is more useful than a false pass. AFTER the first chained entry the same shape is a break.
        Treating it as legacy there reset the chain to GENESIS and returned ``ok``, so editing entry
        k and deleting ``hash``/``prev`` from k onward verified as "ok, N unchained legacy entries":
        the allowance for history older than the chain was a way to launder history newer than it.
        No writer produces that shape — :meth:`record` always writes both fields.

        ``anchor`` and ``anchors`` are :class:`AuditAnchor` records kept outside this file. Without
        them a log missing its newest entries verifies clean, because every link that survived
        still holds. With them, EVERY anchored head must still be one of the chain's digests: if
        one is not, the log is *truncated* (fewer entries than were anchored) or *rewritten*
        (re-chained after an edit that reached back past the anchor). All of them, not the newest:
        a newer anchor may have been taken from the log after it was cut. Entries appended after
        an anchor are fine — the log only grows.

        ``entries`` lets a caller that has already read the file pass what it read, instead of
        re-reading a log that grows for the life of the install.
        """
        entries = self.entries() if entries is None else entries
        prev_hash = GENESIS
        checked = unchained = 0
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            stored = entry.get("hash")
            if not isinstance(stored, str) or not stored:
                if checked:
                    return ChainCheck(
                        False, checked, unchained, index, "unchained entry after the chain began"
                    )
                unchained += 1
                prev_hash = GENESIS  # the chain restarts after a gap it cannot span
                continue
            if entry.get("prev") != prev_hash:
                return ChainCheck(False, checked, unchained, index, "broken link to previous entry")
            if _digest(entry) != stored:
                return ChainCheck(False, checked, unchained, index, "entry content does not match its digest")
            checked += 1
            prev_hash = stored
            seen.add(stored)
        pinned = [*anchors, *([anchor] if anchor is not None else [])]
        # Strongest first, so a break is reported against the anchor that says the most was lost.
        for held in sorted(pinned, key=lambda a: a.count, reverse=True):
            if held.head in seen:
                continue
            if len(entries) < held.count:
                reason = (
                    f"truncated since anchor: {held.count} entries were anchored, "
                    f"the log holds {len(entries)}"
                )
                return ChainCheck(False, checked, unchained, len(entries), reason)
            reason = "rewritten since anchor: the anchored head digest is no longer in the chain"
            # Where the anchored entry should sit; the walk itself found nothing wrong, so this is
            # the best position there is to point at — inside the log, since this branch only runs
            # when the log holds at least `count` entries.
            return ChainCheck(False, checked, unchained, held.count - 1, reason)
        reason = "ok" if not unchained else f"ok, {unchained} unchained legacy entr{'y' if unchained == 1 else 'ies'}"
        return ChainCheck(True, checked, unchained, None, reason)

    @property
    def head(self) -> str:
        """Digest of the newest entry as this instance last saw it; :func:`anchor_of` reads the disk."""
        return self._head

    def __len__(self) -> int:
        return self._count
