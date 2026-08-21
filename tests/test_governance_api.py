"""Tests for the Governance / Security API helpers: the injection scoreboard + the audit reader.

Load-bearing properties: the defenses measurably lower the attack-success-rate (defended ASR <
undefended ASR), the honest gap (``http_exfil``, exfil through an allowed tool) is named in
``leaks_defended``, and the audit reader is newest-first and empty-safe.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.api.governance import read_audit, run_injection_suite


def test_run_injection_suite_defended_beats_undefended_and_names_the_gap() -> None:
    report = run_injection_suite()

    # The defenses lower the attack-success-rate versus the bare baseline.
    assert report["undefended_asr"] > report["defended_asr"]
    assert report["undefended_asr"] == 1.0  # every bare attack lands (the honest baseline)
    assert report["defended_block_rate"] > report["undefended_block_rate"]

    # The honest gap is named out loud: exfil through an allowed tool still gets through.
    assert "http_exfil" in report["leaks_defended"]

    # Shape: totals + per-category + per-attack join all present and consistent.
    assert report["total_attacks"] == 7
    assert len(report["attacks"]) == 7
    cats = {c["category"] for c in report["by_category"]}
    assert cats == {"destructive", "backdoor", "exfil", "self_modify"}
    # Per-category, defended ASR never exceeds the undefended baseline.
    for c in report["by_category"]:
        assert c["defended_asr"] <= c["undefended_asr"]
        assert c["count"] >= 1
    # The named leak shows blocked_defended=False on its attack row.
    http = next(a for a in report["attacks"] if a["id"] == "http_exfil")
    assert http["blocked_defended"] is False and http["blocked_undefended"] is False


def test_read_audit_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    lines = [
        {"seq": 0, "type": "decision", "action": "run_shell", "decision": "deny"},
        {"seq": 1, "type": "decision", "action": "write_file", "decision": "allow"},
        {"seq": 2, "type": "evolution", "change": "skill_added"},
    ]
    path.write_text("\n".join(json.dumps(entry) for entry in lines) + "\n", encoding="utf-8")

    events, _chain = read_audit(path)
    assert [e["seq"] for e in events] == [2, 1, 0]  # newest (highest seq) first


def test_read_audit_missing_file_is_empty(tmp_path: Path) -> None:
    events, chain = read_audit(tmp_path / "nope.jsonl")
    assert events == []
    # An absent log is not a broken one. `ok: False` here would put a tamper warning
    # on the screen of every fresh install.
    assert chain["ok"] is True and chain["checked"] == 0


def test_read_audit_respects_limit(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        "\n".join(json.dumps({"seq": i, "type": "decision"}) for i in range(10)) + "\n",
        encoding="utf-8",
    )
    events, _chain = read_audit(path, limit=3)
    assert [e["seq"] for e in events] == [9, 8, 7]  # newest 3


def test_a_tampered_entry_is_reported_as_a_broken_chain(tmp_path: Path) -> None:
    """The property every write pays for, that nothing ever asked about.

    Each entry carries the digest of the one before it. `AuditLog.verify` has always been able to
    walk that chain and no code, no CLI command and no screen ever called it — so the log detected
    tampering the way an unread smoke alarm detects fire.
    """
    import json

    from chimera.governance.audit import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("taint_narrowed", {"tool": "write_file"})
    log.record("escalated", {"tool": "run_shell"})

    _events, clean = read_audit(path)
    assert clean["ok"] is True and clean["checked"] == 2

    # Edit a line in place, exactly as someone covering their tracks would: the entry still parses,
    # still has a plausible seq, and now says something else.
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["tool"] = "something_harmless"
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _events, broken = read_audit(path)
    assert broken["ok"] is False
    assert broken["broken_at"] == 0
    assert "digest" in broken["reason"]


def test_an_empty_log_is_not_a_broken_one(tmp_path: Path) -> None:
    """`ok: False` on a fresh install would put a tamper warning on every new user's screen."""
    _events, chain = read_audit(tmp_path / "audit.jsonl")

    assert chain["ok"] is True
    assert chain["checked"] == 0 and chain["broken_at"] is None
