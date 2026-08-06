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

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.core.autonomous import AutonomousResult

_log = get_logger("api.runs")

# Serializes concurrent appends to a run log. The Agent Manager runs several autonomous tasks in
# parallel threads, each persisting a receipt to the SAME runs.jsonl; without this a large receipt
# line (diffs) could interleave with another and be dropped as malformed on load. In-process only —
# a single-user local app doesn't share the log across processes.
_append_lock = threading.Lock()


class FileDiffReceipt(BaseModel):
    """One file's real unified diff for an attempt (the machine truth of what that file changed)."""

    path: str = ""
    patch: str = ""  # a difflib unified-diff body (@@ hunks, +/- lines), bounded in the builder
    truncated: bool = False  # the patch was clipped to the char bound


class AttemptReceipt(BaseModel):
    """One attempt's proof: whether it verified, whether it was reverted, and what it changed."""

    index: int = 0
    verified: bool = False
    reverted: bool = False
    success: bool = False
    verify_output: str = ""  # the concrete verifier output (test/assert), truncated in the builder
    diff_summary: str = ""  # the workspace diff this attempt made, as audited before any revert
    feedback: str = ""  # the retry feedback this attempt produced, truncated in the builder
    diffs: list[FileDiffReceipt] = []  # real per-file unified diffs (what the attempt changed/attempted)
    #: On whose authority this attempt was decided: "verifier" | "diff+manager" | "manager" | "none".
    #: This crossed from ``Attempt`` late: the field existed in memory for a release while the
    #: persisted receipt carried only the booleans, so a reader of runs.jsonl could not tell a
    #: command-verified pass from an LLM approving prose. Nobody chose that — it just never crossed
    #: the serialization boundary, which is the quiet way a three-way verdict decays into a two-way one.
    evidence: str = "none"
    #: Whether the workspace changed — ``null`` for "could not be measured", which is NOT "nothing
    #: changed". Serialized explicitly (never omitted) so the unknown survives the round trip; a
    #: field that disappears when it is None is indistinguishable from a field nobody set.
    diff_productive: bool | None = None
    #: What this attempt charged, at list rate — ``null`` when the model's price is unknown, never
    #: 0.0 as a stand-in. Serialized explicitly so the unknown survives the round trip: a receipt
    #: that reports "we do not know" as "free" makes the cheapest-looking configuration the one
    #: nobody can audit.
    usd: float | None = None
    #: The share of ``usd`` that was NOT the worker — planner, manager, checklist, strong-verify.
    #: Split out because it is precisely what a model-per-role profile moves, and for a year it was
    #: priced nowhere at all: the panel built to judge expensive profiles omitted their expense.
    overhead_usd: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""  # the model that actually answered (the EDITOR's, under role routing)
    #: Out-of-checkout side effects this attempt actually performed (send_email, http_post, …).
    #: An empty diff means one thing for a run that only touched files and something else entirely
    #: for a run that already sent mail — the receipt should not force that inference.
    side_effects: list[str] = []


class RunReceipt(BaseModel):
    """One autonomous run: the task, the terminal outcome, and the per-attempt proof trail."""

    ts: str = ""  # ISO-8601 UTC timestamp of the run's completion
    task: str = ""  # the task text, truncated in the builder
    success: bool = False
    paused: bool = False  # interrupted for human approval (never persisted — kept for shape parity)
    verify_command: str | None = None  # the shell command that judged the run, or None (no verifier)
    answer: str = ""  # the final answer, truncated in the builder
    attempts: list[AttemptReceipt] = []
    #: Which model-role configuration ran this — "economy" / "balanced" / "max", or ``null`` for a
    #: run that predates the field or named none. Null is kept as its own group rather than folded
    #: into the default: attributing old runs to a profile they never used would put fabricated
    #: evidence into the one view whose whole job is to say whether a profile was worth it.
    profile: str | None = None
    #: The run's total cost, or ``null`` when ANY attempt's price was unknown. Not a partial sum:
    #: adding up only the legs whose price we happen to know reports a number that is always too
    #: low, and always too low in the direction that flatters whichever configuration used a free
    #: tier. See :func:`total_usd`.
    usd: float | None = None


def total_usd(attempts: list[AttemptReceipt]) -> float | None:
    """Sum the attempts' cost, or ``None`` if any single one is unknown.

    All-or-nothing on purpose. A partial sum is not a conservative estimate — it is a number that
    is confidently wrong in a predictable direction, and the direction is "the run that used a free
    or unpriced model looks cheaper than the one that did not". Refusing to add is the only answer
    that cannot mislead a cost comparison.
    """
    if not attempts:
        return None
    if any(a.usd is None for a in attempts):
        return None
    return round(sum(a.usd or 0.0 for a in attempts), 6)


def build_receipt(
    result: AutonomousResult,
    task: str,
    verify_command: str | None,
    ts: str,
    *,
    profile: str | None = None,
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
            # Bound the per-file diffs defensively again (at most 20 files, each patch <= 4000 chars) so
            # a big run can't bloat runs.jsonl — the caps mirror unified_diffs' own defaults.
            diffs=[
                FileDiffReceipt(
                    path=d.path,
                    patch=(d.patch or "")[:4000],
                    truncated=bool(d.truncated) or len(d.patch or "") > 4000,
                )
                for d in list(getattr(a, "diffs", None) or [])[:20]
            ],
            # getattr with a default keeps this tolerant of the duck-typed result this module
            # accepts by design — an older Attempt without these fields reads as "unknown" rather
            # than raising, which is the honest answer for a record that predates them.
            evidence=getattr(a, "evidence", "none") or "none",
            diff_productive=getattr(a, "diff_productive", None),
            side_effects=list(getattr(a, "side_effects", None) or []),
            usd=getattr(a, "usd", None),
            overhead_usd=getattr(a, "overhead_usd", None),
            prompt_tokens=int(getattr(a, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(a, "completion_tokens", 0) or 0),
            model=str(getattr(a, "model", "") or ""),
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
        profile=profile,
        usd=total_usd(attempts),
    )


def append_run(path: Path, receipt: RunReceipt) -> None:
    """Append one run receipt as a JSON line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = receipt.model_dump_json() + "\n"
    # One writer at a time so parallel-batch receipts can't interleave into a corrupt line.
    with _append_lock, path.open("a", encoding="utf-8") as handle:
        handle.write(line)


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
