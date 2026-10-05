"""The audit chain catches an edit in the middle; on its own it cannot catch the end going missing.

Deleting the newest entries — the ones an incident would make somebody delete — leaves a shorter
chain whose every link still holds, and the Security screen reported it ``ok``. Reproduced before
this change: six entries truncated to three, ``verify()`` answered ``ok=True, checked=3``.

The head digest and the entry count are now written onto every run receipt and every cron result
line, and the screen's check compares the log against the newest of those anchors. Separately, an
entry with its ``hash`` stripped after the chain began used to read as a harmless "legacy" entry,
which let an edit plus a strip pass as ``ok``.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.api.governance import read_audit
from chimera.api.runs import load_runs
from chimera.core import AutonomousAgent, AutonomousConfig
from chimera.core.agent import AgentResult
from chimera.core.verify import VerificationResult
from chimera.governance.audit import GENESIS, AuditLog, _digest


def _filled(home: Path, n: int) -> AuditLog:
    log = AuditLog(home / "audit.jsonl")
    for i in range(n):
        log.record("decision", {"action": f"step {i}"})
    return log


def _keep_first(path: Path, n: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:n]) + "\n", encoding="utf-8")


class _Worker:
    def run(self, task: str) -> AgentResult:
        return AgentResult(answer="done", steps=1, stopped_reason="final")


class _Passes:
    command = "pytest -q"

    def verify(self) -> VerificationResult:
        return VerificationResult(passed=True, output="")


def _receipt(home: Path) -> None:
    """A real finished run writing its receipt — the path that fills the anchor."""
    AutonomousAgent(
        _Worker(),
        verifier=_Passes(),
        run_log=home / "runs.jsonl",
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    ).run("t")


def _cron_line(home: Path) -> None:
    from chimera.scheduler import CronJob
    from chimera.scheduler.delivery import make_deliver

    job = CronJob(id="j1", name="resumo", schedule="0 7 * * *", action="liste")
    make_deliver(home / "scheduler" / "cron_results.jsonl")(job, "a resposta")


# ----------------------------------------------------------------- the downgrade through "legacy"


def test_an_entry_stripped_of_its_digest_after_the_chain_began_is_a_break(tmp_path: Path) -> None:
    """Edit entry 2, strip ``hash``/``prev`` from it onward: that used to verify as ``ok``."""
    log = _filled(tmp_path, 4)
    lines = log.path.read_text(encoding="utf-8").splitlines()
    forged = []
    for index, line in enumerate(lines):
        entry = json.loads(line)
        if index >= 2:
            entry.pop("hash")
            entry.pop("prev")
            if index == 2:
                entry["action"] = "something that never happened"
        forged.append(json.dumps(entry))
    log.path.write_text("\n".join(forged) + "\n", encoding="utf-8")

    check = log.verify()
    assert not check.ok
    assert check.broken_at == 2
    assert "unchained" in check.reason


def test_a_legacy_prefix_before_the_chain_is_still_only_unchained(tmp_path: Path) -> None:
    """The control: entries written before chaining existed are 'cannot tell', not tampering."""
    path = tmp_path / "audit.jsonl"
    path.write_text(json.dumps({"seq": 0, "type": "decision", "action": "x"}) + "\n", encoding="utf-8")
    log = AuditLog(path)
    log.record("decision", {"action": "y"})

    check = log.verify()
    assert check.ok and check.unchained == 1


# ----------------------------------------------------------------------- the anchors themselves


def test_a_run_receipt_carries_the_audit_head_and_count(tmp_path: Path) -> None:
    log = _filled(tmp_path, 10)
    _receipt(tmp_path)

    row = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["audit_count"] == 10
    assert row["audit_head"] == AuditLog(log.path).head
    # And it still loads as a receipt.
    assert load_runs(tmp_path / "runs.jsonl")[0].audit_count == 10


def test_a_cron_result_line_carries_the_audit_head_and_count(tmp_path: Path) -> None:
    log = _filled(tmp_path, 4)
    _cron_line(tmp_path)

    path = tmp_path / "scheduler" / "cron_results.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert row["audit_count"] == 4
    assert row["audit_head"] == AuditLog(log.path).head


def test_no_audit_log_means_no_anchor_rather_than_an_anchor_to_nothing(tmp_path: Path) -> None:
    _receipt(tmp_path)

    row = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["audit_count"] is None and row["audit_head"] == ""
    from chimera.governance.audit import anchor_of

    assert anchor_of(tmp_path / "audit.jsonl") is None


# --------------------------------------------------------------------- what the screen now says


def test_truncating_the_newest_entries_is_caught_against_a_receipt(tmp_path: Path) -> None:
    """The 10-line truncation: three newest entries deleted, every surviving link intact."""
    log = _filled(tmp_path, 10)
    _receipt(tmp_path)
    _keep_first(log.path, 7)

    assert AuditLog(log.path).verify().ok  # the chain alone cannot see it — that is the point
    _events, chain = read_audit(log.path)
    assert chain["ok"] is False
    assert "truncated" in chain["reason"]
    assert chain["broken_at"] == 7  # the first entry that is missing


def test_truncating_the_newest_entries_is_caught_against_a_cron_result(tmp_path: Path) -> None:
    log = _filled(tmp_path, 6)
    _cron_line(tmp_path)
    _keep_first(log.path, 3)

    _events, chain = read_audit(log.path)
    assert chain["ok"] is False
    assert "truncated" in chain["reason"]


def test_a_full_rechain_is_caught_against_the_anchor(tmp_path: Path) -> None:
    """Delete an entry and recompute every digest after it: the chain holds, the anchor does not."""
    log = _filled(tmp_path, 6)
    _receipt(tmp_path)

    entries = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    del entries[3]
    prev = GENESIS
    rows = []
    for seq, entry in enumerate(entries):
        entry["seq"], entry["prev"] = seq, prev
        entry["hash"] = prev = _digest(entry)
        rows.append(json.dumps(entry))
    # Pad back to the anchored length so a count check alone would not notice.
    extra = {"seq": 5, "type": "decision", "action": "filler", "prev": prev}
    extra["hash"] = _digest(extra)
    rows.append(json.dumps(extra))
    log.path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert AuditLog(log.path).verify().ok
    _events, chain = read_audit(log.path)
    assert chain["ok"] is False
    assert "rewritten" in chain["reason"]


def test_entries_written_after_the_anchor_are_not_tampering(tmp_path: Path) -> None:
    """The control for the three above: a log that only grew past its anchor is intact."""
    log = _filled(tmp_path, 5)
    _receipt(tmp_path)
    _cron_line(tmp_path)
    log.record("decision", {"action": "later"})
    log.record("decision", {"action": "later still"})

    _events, chain = read_audit(log.path)
    assert chain["ok"] is True
    assert chain["checked"] == 7


def _last_cron_row(home: Path) -> dict[str, object]:
    path = home / "scheduler" / "cron_results.jsonl"
    row: dict[str, object] = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    return row


# ------------------------------------------------- a host that keeps running after the cut


def test_the_next_cron_tick_and_receipt_do_not_launder_a_truncation(tmp_path: Path) -> None:
    """Anchor, cut, then let the host keep working: the cut must still show.

    Every check above anchors, tampers and reads at once. A real host does not stop: the next tick
    anchored the log as it then stood, and the screen read "ok" again within one tick with nobody
    touching the anchor files. Measured before this test: six entries cut to three, ``ok=False``,
    one more ``deliver()``, ``ok=True, checked=3``.
    """
    log = _filled(tmp_path, 6)
    _cron_line(tmp_path)
    _keep_first(log.path, 3)
    assert read_audit(log.path)[1]["ok"] is False

    _cron_line(tmp_path)  # an ordinary tick
    _receipt(tmp_path)  # and an ordinary run
    log.record("decision", {"action": "life goes on"})
    _cron_line(tmp_path)

    _events, chain = read_audit(log.path)
    assert chain["ok"] is False
    assert "since anchor" in chain["reason"]


def test_a_tick_after_the_cut_repeats_the_anchor_that_broke(tmp_path: Path) -> None:
    """The writer half: what the next tick records is the anchor the log no longer satisfies."""
    log = _filled(tmp_path, 6)
    _cron_line(tmp_path)
    before = _last_cron_row(tmp_path)
    _keep_first(log.path, 3)

    _cron_line(tmp_path)
    after = _last_cron_row(tmp_path)
    assert (after["audit_count"], after["audit_head"]) == (6, before["audit_head"])


def test_a_tick_on_an_intact_log_moves_the_anchor_forward(tmp_path: Path) -> None:
    """The control for the writer: a log that only grew gets a new, stronger anchor."""
    log = _filled(tmp_path, 4)
    _cron_line(tmp_path)
    log.record("decision", {"action": "later"})
    _cron_line(tmp_path)

    row = _last_cron_row(tmp_path)
    assert row["audit_count"] == 5
    assert row["audit_head"] == AuditLog(log.path).head


def test_a_later_anchor_in_the_same_file_cannot_outvote_an_earlier_one(tmp_path: Path) -> None:
    """The reader half, independent of the writer: an anchor taken AFTER the cut must not win.

    Written by hand, as a writer from before this change would have written it. Reading only the
    newest anchored line of each file picked this one and said ``ok``.
    """
    log = _filled(tmp_path, 6)
    _cron_line(tmp_path)
    _keep_first(log.path, 3)
    from chimera.governance.audit import anchor_of

    cut = anchor_of(log.path)
    assert cut is not None
    path = tmp_path / "scheduler" / "cron_results.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "j1", "answer": "x", **cut.fields()}) + "\n")

    _events, chain = read_audit(log.path)
    assert chain["ok"] is False
    assert "truncated" in chain["reason"]


# ------------------------------------------------------------- the count an anchor carries


def test_a_payload_cannot_set_the_seq_an_anchor_counts_from(tmp_path: Path) -> None:
    """``seq`` was written before the payload, so ``{"seq": 999}`` replaced it — and the anchor's
    count is the newest ``seq`` plus one. A bogus count turned "rewritten" into "truncated", or,
    below one, made the line carry no anchor at all."""
    log = _filled(tmp_path, 2)
    entry = log.record("decision", {"action": "x", "seq": 999})
    assert entry["seq"] == 2
    from chimera.governance.audit import anchor_of

    anchor = anchor_of(log.path)
    assert anchor is not None and anchor.count == 3
    assert AuditLog(log.path).verify().ok


def test_the_newest_anchor_is_the_one_compared(tmp_path: Path) -> None:
    """An old receipt at 3 entries must not mask a truncation from 8 to 5."""
    log = _filled(tmp_path, 3)
    _receipt(tmp_path)
    for i in range(5):
        log.record("decision", {"action": f"more {i}"})
    _cron_line(tmp_path)
    _keep_first(log.path, 5)

    _events, chain = read_audit(log.path)
    assert chain["ok"] is False
    assert "truncated" in chain["reason"]
