"""Append-only audit log (JSONL) for governance decisions and evolution changes.

Entries are **hash-chained**: each one carries the digest of the entry before it, and its own digest
over that. Append-only was previously a convention — the file was ordered by a ``seq`` counter and
nothing else, so anyone who could edit the file could rewrite history and leave no trace. A chain
turns that into a detectable event: changing, reordering, or deleting any entry breaks every digest
from that point on, and :meth:`AuditLog.verify` says exactly where.

What this does and does not buy you, stated plainly:

- It **detects** tampering of a log you still hold. It does not **prevent** it.
- An attacker who can rewrite the whole file can also recompute the whole chain. The chain raises the
  bar from "edit one line" to "forge every entry after it"; pinning the head digest somewhere the
  attacker does not control (a receipt, another host) is what closes that gap.
- Entries written before this change carry no digest. :meth:`verify` reports them as *unchained*
  rather than as tampered — an honest "cannot say", not a false pass.

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
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.filelock import locked

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
        # `seq` is not reserved — only `prev` and `hash` are written after the payload, so a payload
        # carrying its own "seq" overwrites it. Counting the lines is the honest answer when one has.
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
        with _process_lock(self.path), locked(self.path):
            seq, prev = self._tail_state()
            entry: dict[str, Any] = {"seq": seq, "type": event_type, **_redacted(payload)}
            # Chain fields are written last on purpose: a payload cannot overwrite them.
            entry["prev"] = prev
            entry["hash"] = _digest(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._count = seq + 1
            self._head = entry["hash"]
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def verify(self) -> ChainCheck:
        """Walk the chain and report the first break.

        A legacy entry (no ``hash``) is counted as *unchained* and skipped rather than failed — the
        log cannot vouch for what predates the chain, and saying so is more useful than a false pass.
        """
        entries = self.entries()
        prev_hash = GENESIS
        checked = unchained = 0
        for index, entry in enumerate(entries):
            stored = entry.get("hash")
            if not isinstance(stored, str) or not stored:
                unchained += 1
                prev_hash = GENESIS  # the chain restarts after a gap it cannot span
                continue
            if entry.get("prev") != prev_hash:
                return ChainCheck(False, checked, unchained, index, "broken link to previous entry")
            if _digest(entry) != stored:
                return ChainCheck(False, checked, unchained, index, "entry content does not match its digest")
            checked += 1
            prev_hash = stored
        reason = "ok" if not unchained else f"ok, {unchained} unchained legacy entr{'y' if unchained == 1 else 'ies'}"
        return ChainCheck(True, checked, unchained, None, reason)

    @property
    def head(self) -> str:
        """Digest of the newest entry — pin this externally to detect a wholesale rewrite."""
        return self._head

    def __len__(self) -> int:
        return self._count
