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

    events = read_audit(path)
    assert [e["seq"] for e in events] == [2, 1, 0]  # newest (highest seq) first


def test_read_audit_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_audit(tmp_path / "nope.jsonl") == []


def test_read_audit_respects_limit(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        "\n".join(json.dumps({"seq": i, "type": "decision"}) for i in range(10)) + "\n",
        encoding="utf-8",
    )
    events = read_audit(path, limit=3)
    assert [e["seq"] for e in events] == [9, 8, 7]  # newest 3
