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

import os
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

    discarded_at: str = ""
    """Where the reverted work was written IN FULL, or "" when nothing was reverted.

    `diffs` below is bounded at 4,000 chars per patch so this file stays readable, and that bound
    makes it a souvenir rather than a recovery: measured on a real run, a 581-line `index.html` was
    reverted and the clipped patch was the only copy left of it."""

    index: int = 0
    verified: bool = False
    #: Digest of the tree at the moment this verdict was given, or "" when nothing captured it.
    #: Kept so ``delivered_matches_verified`` on the run can be recomputed later by a reader, rather
    #: than only believed — the whole point of the field is that a claim be checkable.
    verified_fingerprint: str = ""
    reverted: bool = False
    success: bool = False
    verify_output: str = ""  # the concrete verifier output (test/assert), truncated in the builder
    diff_summary: str = ""  # the workspace diff this attempt made, as audited before any revert
    feedback: str = ""  # the retry feedback this attempt produced, truncated in the builder
    diffs: list[FileDiffReceipt] = []  # real per-file unified diffs (what the attempt changed/attempted)
    #: On whose authority this attempt was decided: "verifier" | "diff+manager" | "diff" | "manager"
    #: | "none". "diff" is a measured workspace change with no manager configured — what used to be
    #: mislabelled "manager", naming a reviewer that never ran.
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
    #: The trace line this attempt wrote, by id. The join key between what a run ACHIEVED (here) and
    #: what it was CARRYING (the step log) — the pair nobody could compute while the trace was keyed
    #: by a truncated task. Empty for an attempt that ran without tracing, and for every receipt
    #: written before this field existed.
    run_id: str = ""
    #: Out-of-checkout side effects this attempt actually performed (send_email, http_post, …).
    #: An empty diff means one thing for a run that only touched files and something else entirely
    #: for a run that already sent mail — the receipt should not force that inference.
    side_effects: list[str] = []
    #: Every tool this attempt called, in order — the last leg of a wire that used to end at the
    #: loop. A receipt said what an attempt COST and never what it DID, so "how many edits did this
    #: task take?" was unanswerable from a finished run, and `bench/edit_tools/` could not read its
    #: own primary metric. Capped like the other bounded fields: a pathological run must not bloat
    #: `runs.jsonl`, and 200 calls is far past the point where the sequence is still being read.
    tool_names: list[str] = []


class RunReceipt(BaseModel):
    """One autonomous run: the task, the terminal outcome, and the per-attempt proof trail."""

    ts: str = ""  # ISO-8601 UTC timestamp of the run's completion
    task: str = ""  # the task text, truncated in the builder
    success: bool = False
    paused: bool = False  # interrupted for human approval (never persisted — kept for shape parity)
    verify_command: str | None = None  # the shell command that judged the run, or None (no verifier)
    profile_source: str = "user"
    """Who chose ``run_profile``: ``user`` or ``system``. Defaults to ``user`` because every receipt
    written before the screen stopped asking came from a deliberate pick."""

    verify_source: str = "user"
    """Where ``verify_command`` came from: ``user`` | ``inferred:<file>`` | ``none``.

    A receipt that cannot tell a typed command from one this app read off the project is a receipt
    that will eventually be cited as evidence of a decision nobody made. The default is ``user``
    because every receipt written before this field existed came from a typed command — the field
    was the only way to supply one."""
    answer: str = ""  # the final answer, truncated in the builder

    delivered_matches_verified: bool | None = None
    """Is the tree on disk still the one the winning attempt's verdict was about?

    ``verified`` is a statement about an instant; this row is read as a statement about the
    delivery, and those came apart in a measured run — the receipt said ``verified: True`` with
    ``Ran 20 tests ... OK`` stored beside it, and the delivered tree failed all twenty on every one
    of twenty executions, because a line present in the stored diff was missing from the file.

    ``None`` means not checkable (no successful attempt, no workspace guard, or a row written before
    the check existed) and is deliberately not ``True``: "we did not look" and "we looked and it
    matched" are different claims, and defaulting to the stronger one would stamp it on every old
    row in the file."""

    stopped_reason: str = ""
    """Why the loop stopped: ``final`` | ``max_steps`` | ``tool_loop`` | ``budget`` | ``spend`` |
    ``cancelled``.

    The loop has always known this — it is assigned at one place per value, reaches ``traces.jsonl``
    and the SSE ``done`` frame, and draws a badge on screen. It stopped at this boundary, so a run
    the user cancelled, a run cut off by the dollar ceiling and a run whose work the verifier
    rejected all persisted the same ``success: false``. The file kept as the durable record could
    not answer "how many runs stopped at the cap this month".

    Empty means a receipt written before this field existed. Not filled in with a plausible guess,
    for the reason ``workspace`` and ``profile`` give above: attributing an outcome a run may not
    have had would put invented evidence into the one view whose job is to say what happened."""

    attempts: list[AttemptReceipt] = []
    #: Which model-role configuration ran this — "economy" / "balanced" / "max", or ``null`` for a
    #: run that predates the field or named none. Null is kept as its own group rather than folded
    #: into the default: attributing old runs to a profile they never used would put fabricated
    #: evidence into the one view whose whole job is to say whether a profile was worth it.
    profile: str | None = None
    workspace: str = ""
    """The project this run happened in. Empty for a run that predates the field, or one whose agent
    was built without a workspace.

    Empty is kept as its own value rather than filled in with a plausible guess: attributing a run
    to a project it may not have come from would put fabricated evidence into the two views whose
    entire job is to judge what happened where. A per-project filter must show those runs as
    unattributed, never as belonging to whichever project is open."""

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
    verify_source: str = "user",
    profile_source: str = "user",
    workspace: str = "",
    delivered_matches_verified: bool | None = None,
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
            run_id=getattr(a, "run_id", "") or "",
            diff_productive=getattr(a, "diff_productive", None),
            side_effects=list(getattr(a, "side_effects", None) or []),
            tool_names=[str(n) for n in (getattr(a, "tool_names", None) or [])][:200],
            usd=getattr(a, "usd", None),
            overhead_usd=getattr(a, "overhead_usd", None),
            prompt_tokens=int(getattr(a, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(a, "completion_tokens", 0) or 0),
            model=str(getattr(a, "model", "") or ""),
            discarded_at=str(getattr(a, "discarded_at", "") or ""),
            verified_fingerprint=str(getattr(a, "verified_fingerprint", "") or ""),
        )
        for a in result.attempts
    ]
    return RunReceipt(
        ts=ts,
        task=(task or "")[:2000],
        success=result.success,
        paused=result.paused,
        verify_command=verify_command,
        verify_source=verify_source,
        profile_source=profile_source,
        answer=(result.answer or "")[:2000],
        # `getattr`, like every other field read off `result` here: this builder takes duck-typed
        # results from several call sites, and a required read would turn a new field into a crash
        # on the path that persists the run — after the work was already paid for.
        stopped_reason=str(getattr(result, "stopped_reason", "") or ""),
        attempts=attempts,
        profile=profile,
        workspace=workspace,
        usd=total_usd(attempts),
        delivered_matches_verified=delivered_matches_verified,
    )


def append_run(path: Path, receipt: RunReceipt) -> None:
    """Append one run receipt as a JSON line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = receipt.model_dump_json() + "\n"
    # One writer at a time so parallel-batch receipts can't interleave into a corrupt line.
    with _append_lock, path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def same_folder(a: str, b: str) -> bool:
    """Whether two spellings name the same folder, without touching the disk.

    ``==`` on the raw strings was the filter, and on Windows one folder has several spellings.
    Measured in the app: two runs started with forward slashes returned nothing when the list asked
    for the same folder spelled with the OS separator. The runs were recorded correctly and simply
    never appeared under their own project — which reads as the app having lost them.

    ``normpath`` settles the separator, a trailing one, and any ``.``/``..``; ``normcase`` settles
    the case, and only where the platform says case is not significant — on POSIX it is the
    identity, so two folders differing in case stay two folders.

    Deliberately NOT ``resolve()``: that reads the filesystem, follows symlinks and would make a
    list of receipts depend on which of them still exist on disk.
    """
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _in_project(receipt_workspace: str, workspace: str) -> bool:
    """Whether a receipt belongs to the project being asked for.

    ``""`` is a query in its own right, not "no filter": it asks for the receipts that have NO
    workspace, which is the only way to reach them once any filter is applied. Comparing it as a
    path would break that — ``normpath("")`` is ``"."``, so an unattributed receipt would answer to
    a query for the current directory and, worse, ``""`` would stop finding the receipts it exists
    to find.
    """
    if not workspace:
        return not receipt_workspace
    return bool(receipt_workspace) and same_folder(receipt_workspace, workspace)


def load_runs(path: Path, *, workspace: str | None = None) -> list[RunReceipt]:
    """Load persisted run receipts; malformed lines are skipped.

    ``workspace`` filters to one project. ``None`` (the default) returns everything, which is what
    every existing caller means and what a client with no project in hand should get.

    A receipt with no workspace is NOT included in a filtered result. It predates the field or came
    from an agent built without one, and there is no honest way to place it: showing it under
    whichever project happens to be open is fabricated evidence in the one view whose job is to say
    what a configuration was worth, and it would be fabricated differently for each reader. It is
    still reachable by asking for ``""`` — see :func:`_in_project`.
    """
    path = Path(path)
    if not path.exists():
        return []
    out: list[RunReceipt] = []
    # Line by line, not `read_text().splitlines()`. Measured on a 5.5 MB log of 1000 receipts: the
    # whole-file read peaks at 22 MB — four times the file, because the raw bytes, the decoded
    # string and the list of lines are all alive at once — against 0.1 MB streaming. Three routes
    # read this file and a receipt embeds its diffs, so the log grows at ~5.6 KB a run and the
    # multiplier travels with it. The parse time is NOT the problem: 1000 receipts parse in 24 ms.
    with path.open(encoding="utf-8", errors="replace") as arquivo:
        for line in arquivo:
            if not line.strip():
                continue
            try:
                receipt = RunReceipt.model_validate_json(line)
            except ValueError:  # pragma: no cover - defensive
                _log.warning("skipping malformed run receipt line")
                continue
            if workspace is None or _in_project(receipt.workspace, workspace):
                out.append(receipt)
    return out
