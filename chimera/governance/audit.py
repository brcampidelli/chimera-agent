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
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class AuditLog:
    """An append-only, hash-chained JSONL audit trail."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        entries = self.entries()
        self._count = len(entries)
        # Resume the chain from whatever is already on disk, so appending to an existing log keeps
        # one continuous chain instead of silently starting a second one.
        last = entries[-1] if entries else None
        self._head = str(last.get("hash", "")) if last else GENESIS
        if not self._head:  # legacy tail with no digest — chain restarts, and verify() will say so
            self._head = GENESIS

    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"seq": self._count, "type": event_type, **payload}
        # Chain fields are written last on purpose: a payload cannot overwrite them.
        entry["prev"] = self._head
        entry["hash"] = _digest(entry)
        self._count += 1
        self._head = entry["hash"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
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
