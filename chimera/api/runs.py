"""Run receipts: one append-only JSONL record per autonomous run, exposing how a run PROVED its work.

Mirrors :mod:`chimera.api.usage` (Pydantic ``.model_dump_json()`` per line, malformed lines skipped
on load). A receipt captures ONLY real, already-computed evidence from ``AutonomousResult``: the
verify-or-revert trail per attempt (verified / reverted / success), the diff the attempt actually made
to the workspace, and the verify command that judged it. Nothing here is fabricated — an attempt that
recorded no diff or no verify output shows the empty string, never invented detail.

``build_receipt`` accepts the result by duck typing so this module never imports ``autonomous`` at the
top (autonomous imports THIS module — a top-level import would be circular); the ``TYPE_CHECKING``
guard supplies the types for static checking only.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.core.autonomous import AutonomousResult

_log = get_logger("api.runs")


class AttemptReceipt(BaseModel):
    """One attempt's proof: whether it verified, whether it was reverted, and what it changed."""

    index: int = 0
    verified: bool = False
    reverted: bool = False
    success: bool = False
    verify_output: str = ""  # the concrete verifier output (test/assert), truncated in the builder
    diff_summary: str = ""  # the workspace diff this attempt made, as audited before any revert
    feedback: str = ""  # the retry feedback this attempt produced, truncated in the builder


class RunReceipt(BaseModel):
    """One autonomous run: the task, the terminal outcome, and the per-attempt proof trail."""

    ts: str = ""  # ISO-8601 UTC timestamp of the run's completion
    task: str = ""  # the task text, truncated in the builder
    success: bool = False
    paused: bool = False  # interrupted for human approval (never persisted — kept for shape parity)
    verify_command: str | None = None  # the shell command that judged the run, or None (no verifier)
    answer: str = ""  # the final answer, truncated in the builder
    attempts: list[AttemptReceipt] = []


def build_receipt(
    result: AutonomousResult, task: str, verify_command: str | None, ts: str
) -> RunReceipt:
    """Map an ``AutonomousResult`` (and its attempts) into a receipt, truncating the bounded fields."""
    attempts = [
        AttemptReceipt(
            index=a.index,
            verified=a.verified,
            reverted=a.reverted,
            success=a.success,
            verify_output=(a.verify_output or "")[:4000],
            diff_summary=a.diff_summary or "",
            feedback=(a.feedback or "")[:1000],
        )
        for a in result.attempts
    ]
    return RunReceipt(
        ts=ts,
        task=(task or "")[:2000],
        success=result.success,
        paused=result.paused,
        verify_command=verify_command,
        answer=(result.answer or "")[:2000],
        attempts=attempts,
    )


def append_run(path: Path, receipt: RunReceipt) -> None:
    """Append one run receipt as a JSON line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(receipt.model_dump_json() + "\n")


def load_runs(path: Path) -> list[RunReceipt]:
    """Load persisted run receipts; malformed lines are skipped."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[RunReceipt] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(RunReceipt.model_validate_json(line))
        except ValueError:  # pragma: no cover - defensive
            _log.warning("skipping malformed run receipt line")
    return out
