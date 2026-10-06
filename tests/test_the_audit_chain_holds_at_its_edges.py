"""The audit chain at its edges — what the mutation gate found nothing asserting (study 30, S30-37).

`audit.py` went into the mutation gate and 114 of its mutants survived the tests that existed. Most
of them were not exotic: the exact verdict `verify()` returns (its counts, its index, its reason),
the canonical form the digest is taken over, a log line longer than one read of the tail, a garbage
line in the middle of the file. Each test below pins one of those by what the Security screen and
the anchors actually consume. None of them found a defect in the code; they find the next one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import chimera.governance.audit as audit_mod
from chimera.governance.audit import (
    GENESIS,
    AuditAnchor,
    AuditLog,
    ChainCheck,
    _digest,
    _heads_on_disk,
    _last_entry,
    anchor_of,
    recorded_anchors,
)


def _log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8", newline="\n")


# --- the digest is an on-disk format ------------------------------------------------------------


def test_the_digest_is_sha256_over_sorted_compact_utf8_json() -> None:
    # Every log ever written is verified against this exact canonical form. Change the key order,
    # the separators or the escaping and every existing install's log reports itself as tampered.
    entry = {"seq": 0, "type": "decision", "zeta": "é", "alpha": [1, {"b": 2, "a": 1}],
             "prev": GENESIS, "hash": "ignored"}
    body = {k: v for k, v in entry.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert canonical.startswith('{"alpha":[1,{"a":1,"b":2}],"prev":')
    assert _digest(entry) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert _digest(entry) == "3542fd368a977d298f80cbded02c74c1ec4c368cb5b4f6fd9b09d7c5bea121f3"


def test_an_entry_keeps_its_key_order_and_its_text_unescaped(tmp_path: Path) -> None:
    log = _log(tmp_path)
    entry = log.record("decision", {"action": "café", "seq": 99})
    assert list(entry) == ["seq", "type", "action", "prev", "hash"]
    assert entry["seq"] == 0
    # Written as text a person can read, not as \u escapes.
    assert "café" in log.path.read_text(encoding="utf-8")


def test_a_log_in_a_folder_that_does_not_exist_yet_is_created(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a" / "b" / "audit.jsonl")
    log.record("decision", {})
    assert log.verify().ok


# --- verify() returns a verdict, and every field of it is read -----------------------------------


def test_a_clean_log_verifies_with_the_exact_verdict(tmp_path: Path) -> None:
    log = _log(tmp_path)
    for i in range(3):
        log.record("decision", {"i": i})
    assert log.verify() == ChainCheck(True, 3, 0, None, "ok")


def test_a_legacy_prefix_is_counted_and_named_in_the_singular_and_the_plural(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_lines(path, [json.dumps({"seq": 0, "type": "old"})])
    log = AuditLog(path)
    assert log.head == GENESIS  # a legacy tail restarts the chain, never "None" or ""
    log.record("decision", {})
    assert log.verify() == ChainCheck(True, 1, 1, None, "ok, 1 unchained legacy entry")

    path2 = tmp_path / "two.jsonl"
    # An empty "hash" is as unchained as a missing one.
    _write_lines(path2, [json.dumps({"seq": 0}), json.dumps({"seq": 1, "hash": ""})])
    log2 = AuditLog(path2)
    assert log2.head == GENESIS
    log2.record("decision", {})
    log2.record("decision", {})
    assert log2.verify() == ChainCheck(True, 2, 2, None, "ok, 2 unchained legacy entries")


def test_an_unchained_entry_after_the_chain_is_a_break_with_its_counts(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record("decision", {})
    log.record("decision", {})
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"seq": 2, "type": "forged"}) + "\n")
    check = log.verify()
    assert check.ok is False
    assert check == ChainCheck(False, 2, 0, 2, "unchained entry after the chain began")


def test_a_broken_link_and_a_bad_digest_are_reported_with_their_counts(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_lines(path, [json.dumps({"seq": 0})])  # one legacy entry first, so `unchained` is 1
    log = AuditLog(path)
    for i in range(3):
        log.record("decision", {"i": i})
    lines = path.read_text(encoding="utf-8").splitlines()

    relinked = json.loads(lines[2])
    relinked["prev"] = "f" * 64
    _write_lines(path, [lines[0], lines[1], json.dumps(relinked), lines[3]])
    check = log.verify()
    assert check.ok is False
    assert check == ChainCheck(False, 1, 1, 2, "broken link to previous entry")

    edited = json.loads(lines[2])
    edited["i"] = 99
    _write_lines(path, [lines[0], lines[1], json.dumps(edited), lines[3]])
    check = log.verify()
    assert check.ok is False
    assert check == ChainCheck(False, 1, 1, 2, "entry content does not match its digest")


def _anchored(tmp_path: Path, n: int) -> tuple[AuditLog, list[AuditAnchor]]:
    log = _log(tmp_path)
    anchors = []
    for i in range(n):
        log.record("decision", {"i": i})
        anchor = anchor_of(log.path)
        assert anchor is not None
        anchors.append(anchor)
    return log, anchors


def test_a_truncation_is_reported_against_the_strongest_anchor(tmp_path: Path) -> None:
    log, anchors = _anchored(tmp_path, 5)
    lines = log.path.read_text(encoding="utf-8").splitlines()
    _write_lines(log.path, lines[:2])
    # Both the 4- and the 5-entry anchors are gone; the report names the one that says most was lost.
    check = log.verify(anchors=[anchors[3], anchors[4], anchors[0]])
    assert check == ChainCheck(
        False, 2, 0, 2, "truncated since anchor: 5 entries were anchored, the log holds 2"
    )


def test_a_rewrite_is_reported_where_the_anchored_entry_should_sit(tmp_path: Path) -> None:
    log, anchors = _anchored(tmp_path, 4)
    # Re-chain the whole log after editing entry 1: every link holds, the anchored heads are gone.
    entries = log.entries()
    entries[1]["i"] = 99
    prev = GENESIS
    rewritten = []
    for entry in entries:
        entry["prev"] = prev
        entry["hash"] = _digest(entry)
        prev = entry["hash"]
        rewritten.append(json.dumps(entry, ensure_ascii=False))
    _write_lines(log.path, rewritten)
    check = log.verify(anchor=anchors[2], anchors=[anchors[1]])
    assert check == ChainCheck(
        False, 4, 0, 2, "rewritten since anchor: the anchored head digest is no longer in the chain"
    )


def test_every_anchor_is_checked_not_only_the_first_that_holds(tmp_path: Path) -> None:
    log, anchors = _anchored(tmp_path, 3)
    # An anchor whose head is still present must not end the check: a weaker one that is gone is
    # still a rewrite. (A strong anchor taken after the log was re-chained would otherwise bless it.)
    forged = AuditAnchor(1, "e" * 64)
    check = log.verify(anchors=[anchors[2], forged])
    assert check.ok is False
    assert check.broken_at == 0


# --- reading the tail: lines longer than one read ------------------------------------------------


def _record_with_line_length(log: AuditLog, target: int) -> dict[str, Any]:
    """Record one entry whose line (without its newline) is exactly ``target`` bytes long."""
    probe = log.path.with_suffix(".probe")
    trial = AuditLog(probe)
    trial.record("blob", {"blob": ""})
    base = len(probe.read_bytes().rstrip(b"\n"))
    probe.unlink()
    return log.record("blob", {"blob": "a" * (target - base)})


def test_a_newest_entry_longer_than_one_tail_read_is_still_found(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record("decision", {})
    big = log.record("blob", {"blob": "a" * (3 * audit_mod._TAIL_CHUNK)})
    last = _last_entry(log.path)
    assert last is not None and last["hash"] == big["hash"]
    after = log.record("decision", {})
    assert after["seq"] == 2 and after["prev"] == big["hash"]
    assert log.verify() == ChainCheck(True, 3, 0, None, "ok")


def test_a_single_line_one_byte_longer_than_a_tail_read_is_read_whole(tmp_path: Path) -> None:
    # The file is exactly one chunk plus its newline: the loop has to take one more one-byte bite.
    log = _log(tmp_path)
    first = _record_with_line_length(log, audit_mod._TAIL_CHUNK)
    assert log.path.stat().st_size == audit_mod._TAIL_CHUNK + 1
    last = _last_entry(log.path)
    assert last is not None and last["hash"] == first["hash"]


# --- what the tail says when a line is not what record() writes ----------------------------------


def test_seq_is_read_from_the_newest_entry_not_from_counting_lines(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record("decision", {})
    second = log.record("decision", {})
    lines = log.path.read_text(encoding="utf-8").splitlines()
    _write_lines(log.path, [lines[0], "{torn by a crash", lines[1]])
    anchor = anchor_of(log.path)
    assert anchor == AuditAnchor(2, second["hash"])  # three lines, but the newest seq is 1
    assert log.record("decision", {})["seq"] == 2


def test_a_newest_entry_with_no_usable_seq_falls_back_to_counting_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_lines(path, [json.dumps({"x": 1}), json.dumps({"seq": "7", "hash": "c" * 64})])
    anchor = anchor_of(path)
    assert anchor == AuditAnchor(2, "c" * 64)
    entry = AuditLog(path).record("decision", {})
    assert entry["seq"] == 2 and entry["prev"] == "c" * 64


@pytest.mark.parametrize("head", ["", 5])
def test_a_newest_entry_without_a_digest_gives_no_anchor_and_restarts_the_chain(
    tmp_path: Path, head: object
) -> None:
    path = tmp_path / "audit.jsonl"
    _write_lines(path, [json.dumps({"seq": 0, "hash": head})])
    assert anchor_of(path) is None
    assert AuditLog(path).record("decision", {})["prev"] == GENESIS


def test_a_blank_line_in_the_middle_hides_no_entry_after_it(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record("decision", {})
    log.record("decision", {})
    lines = log.path.read_text(encoding="utf-8").splitlines()
    _write_lines(log.path, [lines[0], "", lines[1]])
    reopened = AuditLog(log.path)
    assert len(reopened) == 2
    assert [e["seq"] for e in reopened.entries()] == [0, 1]


def test_the_head_scan_skips_garbage_without_stopping_or_inventing_a_head(tmp_path: Path) -> None:
    log = _log(tmp_path)
    first = log.record("decision", {})
    with log.path.open("ab") as handle:
        handle.write(b"short\n")
        # The mark at the right offset, but the line does not end the way record() ends a line.
        handle.write(b'{"pad": "' + b"p" * 20 + b'", "hash": "' + b"d" * 64 + b'xy\n')
        # Non-ASCII where a head would be: must be skipped or replaced, never crash the scan.
        handle.write(b'{"pad": "' + b"p" * 20 + b'", "hash": "' + b"\xff" * 64 + b'"}\n')
    second = log.record("decision", {})
    heads = _heads_on_disk(log.path)
    assert first["hash"] in heads and second["hash"] in heads
    assert "d" * 64 not in heads


def test_the_head_scan_s_length_floor_is_no_defence_against_a_forged_line(tmp_path: Path) -> None:
    """Pins the premise of the allowlisted `<`→`<=` mutant of the floor (audit mutmut_13).

    The two versions differ only on a line of exactly `"hash": "<64>"}`. That entry is equivalent
    because (1) record() never writes a line that short, and (2) a forgery one byte longer is read
    as a head anyway — so where the floor sits changes nothing an attacker can do. If either stops
    being true, the allowlist reason is false and this fails.
    """
    log = _log(tmp_path)
    for payload in ({}, {"k": ""}):
        log.record("", payload)
    floor = len(b'"hash": "') + len(GENESIS) + len(b'"}')
    shortest = min(len(raw.rstrip()) for raw in log.path.read_bytes().splitlines())
    assert shortest > floor + 1
    forged = "f" * 64
    with log.path.open("ab") as handle:
        handle.write(b'x"hash": "' + forged.encode() + b'"}\n')
    assert forged in _heads_on_disk(log.path)


def test_every_anchor_in_the_tail_is_read_strongest_first(tmp_path: Path) -> None:
    home = tmp_path
    lines = [
        json.dumps({"audit_count": 2, "audit_head": "a" * 64}),
        "not json at all",
        json.dumps({"audit_count": 5, "audit_head": "b" * 64}),
        json.dumps({"audit_count": 5, "audit_head": "b" * 64}),
        json.dumps({"audit_count": 3, "audit_head": "c" * 64}),
    ]
    _write_lines(home / "runs.jsonl", lines)
    assert recorded_anchors(home) == [
        AuditAnchor(5, "b" * 64), AuditAnchor(3, "c" * 64), AuditAnchor(2, "a" * 64),
    ]


# --- under contention ------------------------------------------------------------------------------


def test_the_lock_retries_back_off_longer_each_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(handle: object) -> None:
        raise OSError("lock unavailable (test)")

    waits: list[float] = []
    monkeypatch.setattr("chimera.core.filelock._acquire", _refuse)
    # The audit module's own clock only: the file lock's internals keep the real one.
    monkeypatch.setattr(audit_mod, "time", SimpleNamespace(sleep=waits.append))
    log = _log(tmp_path)
    log.record("decision", {})
    assert waits == [audit_mod._LOCK_BACKOFF_S * 1, audit_mod._LOCK_BACKOFF_S * 2]
    assert len(log) == 1


def test_an_append_that_got_its_lock_logs_no_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.ERROR, logger="chimera.governance.audit"):
        _log(tmp_path).record("decision", {})
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_an_unreadable_line_is_reported_by_its_line_number(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    path = tmp_path / "audit.jsonl"
    _write_lines(path, [json.dumps({"seq": 0}), "{torn"])
    with caplog.at_level(logging.WARNING, logger="chimera.governance.audit"):
        AuditLog(path).entries()
    assert any("unreadable audit line 2 " in r.getMessage() for r in caplog.records)


def test_two_audit_files_never_share_a_process_lock(tmp_path: Path) -> None:
    # The process lock exists so two writers of ONE file in one process take turns; keyed by the
    # file, two different logs (a project's and the home's) do not queue behind each other, and
    # two spellings of the same file still share one.
    one = audit_mod._process_lock(tmp_path / "a" / "audit.jsonl")
    assert audit_mod._process_lock(tmp_path / "a" / "audit.jsonl") is one
    assert audit_mod._process_lock(tmp_path / "a" / "." / "audit.jsonl") is one
    assert audit_mod._process_lock(tmp_path / "b" / "audit.jsonl") is not one


def test_an_append_creates_its_own_folder_whatever_the_lock_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Today the file lock happens to create the folder for its `.lock` file first. The append must
    # not lean on that: a lock that cannot be taken (read-only lock dir, another lock backend) would
    # otherwise turn the first entry of a new log into a FileNotFoundError.
    from collections.abc import Iterator
    from contextlib import contextmanager

    @contextmanager
    def lock_that_touches_nothing(_path: Path) -> Iterator[bool]:
        yield True

    monkeypatch.setattr(audit_mod, "locked", lock_that_touches_nothing)
    log = AuditLog(tmp_path / "deep" / "er" / "audit.jsonl")
    log.record("decision", {})
    assert log.verify() == ChainCheck(True, 1, 0, None, "ok")
