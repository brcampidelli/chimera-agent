"""The audit log must make tampering *detectable* — append-only by convention is not enough."""

from __future__ import annotations

import json
from pathlib import Path

from chimera.governance.audit import GENESIS, AuditLog


def _log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


def test_entries_are_chained_to_their_predecessor(tmp_path: Path) -> None:
    log = _log(tmp_path)
    first = log.record("decision", {"action": "allow"})
    second = log.record("decision", {"action": "block"})

    assert first["prev"] == GENESIS
    assert second["prev"] == first["hash"]
    assert log.verify().ok


def test_editing_an_entry_breaks_the_chain_at_that_entry(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record("decision", {"action": "block"})
    log.record("decision", {"action": "allow"})

    # Rewrite history: flip the first decision, leaving everything else untouched.
    lines = log.path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["action"] = "allow"
    lines[0] = json.dumps(entry, ensure_ascii=False)
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    check = log.verify()
    assert not check.ok
    assert check.broken_at == 0
    assert "digest" in check.reason


def test_deleting_an_entry_is_detected(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record("decision", {"action": "one"})
    log.record("decision", {"action": "two"})
    log.record("decision", {"action": "three"})

    lines = log.path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # drop the middle entry
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    check = log.verify()
    assert not check.ok
    assert check.broken_at == 1  # the survivor now points at a hash that is no longer its neighbour


def test_a_payload_cannot_forge_its_own_chain_fields(tmp_path: Path) -> None:
    """The audited party must not be able to write the fields that audit it."""
    log = _log(tmp_path)
    log.record("decision", {"action": "allow"})
    entry = log.record("decision", {"action": "allow", "prev": "deadbeef", "hash": "cafe"})

    assert entry["prev"] != "deadbeef"
    assert entry["hash"] != "cafe"
    assert log.verify().ok


def test_appending_after_reopen_continues_one_chain(tmp_path: Path) -> None:
    first = _log(tmp_path)
    first.record("decision", {"action": "one"})
    head = first.head

    reopened = _log(tmp_path)
    entry = reopened.record("decision", {"action": "two"})

    assert entry["prev"] == head
    assert reopened.verify().ok
    assert len(reopened) == 2


def test_legacy_unchained_entries_are_reported_not_silently_passed(tmp_path: Path) -> None:
    """A log written before chaining cannot be vouched for — say 'cannot tell', never 'fine'."""
    path = tmp_path / "audit.jsonl"
    path.write_text(json.dumps({"seq": 0, "type": "decision", "action": "allow"}) + "\n", encoding="utf-8")

    log = AuditLog(path)
    log.record("decision", {"action": "block"})

    check = log.verify()
    assert check.ok  # nothing is provably tampered
    assert check.unchained == 1  # but one entry carries no proof either way
    assert "unchained" in check.reason
