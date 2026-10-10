"""Append-only provider observations and comparison with harness steplogs.

The wire log is written by the gateway at call time; the trace is written by the run when it ends.
:func:`reconcile` reads both and reports where they disagree. Since S30-61 Amendment 5 every wire
record carries the KIND of call and the RUN it belongs to, both declared by the caller
(:mod:`chimera.governance.wire_context`), and reconciliation is per run:

* a ``step`` record is compared one to one with a ``StepRecord`` (omission, fabrication, altered
  copy — as before);
* any other kind in a traced run must be CLAIMED by that run's trace (``side_calls``) with the same
  kind and digests, and the claim must fit what the trace says happened: a summary follows a step
  that compacted, a closing call belongs to a run that stopped at ``max_steps``, on the loop breaker
  or on a handover, an empty-reply retry follows a step that called no tool;
* records of a run that promised a trace and has none (it raised, its trace rotated out, or the
  line was removed) are their own category, never silently dropped;
* records written outside any run (cron, judges, bots) are counted by kind, not compared;
* records written before kinds existed carry none: they are read exactly as before (``unknown``)
  and the result says how many there were.

What the kind does NOT do is make the trace's word evidence: the wire record's kind is fixed when the
call is made, so a later edit to the trace cannot turn a step into a summary, but a harness that lies
at call time is the both-writers-compromised case the registration excludes.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.governance.wire_context import CLOSE, EMPTY_RETRY, STEP, SUMMARY

_LOCK = threading.Lock()

#: The stop reasons under which `Agent._close` makes its closing call.
_CLOSING_STOPS = frozenset({"max_steps", "tool_loop", "handover"})
#: How a record written before kinds existed is reported.
UNKNOWN = "unknown"


def digest(value: Any) -> str:
    """Return a stable digest without retaining the value being fingerprinted."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def append_wire_record(
    path: Path,
    *,
    model: str,
    request_digest: str,
    response_digest: str,
    kind: str | None = None,
    run_id: str | None = None,
    traced: bool | None = None,
) -> str:
    """Append one metadata-only event; the payload and credentials never reach disk.

    ``kind``, ``run_id`` and ``traced`` are written only when given, so a record from a caller that
    declares nothing keeps the original five fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record_id = uuid.uuid4().hex
    record: dict[str, Any] = {
        "wire_id": record_id,
        "model": model,
        "request_digest": request_digest,
        "response_digest": response_digest,
        "ts": datetime.now(UTC).isoformat(),
    }
    if kind is not None:
        record["kind"] = kind
    if run_id is not None:
        record["run_id"] = run_id
        record["traced"] = bool(traced)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    with _LOCK:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)
    return record_id


class _Audit:
    """The discrepancies found, each attributed to the run it belongs to ("" for none)."""

    def __init__(self) -> None:
        self.found: dict[str, list[Any]] = defaultdict(list)
        self.by_run: Counter[str] = Counter()

    def add(self, field: str, value: Any, run: str) -> None:
        self.found[field].append(value)
        self.by_run[run] += 1


def reconcile(wire_path: Path, steplog_path: Path, *, run_id: str | None = None) -> dict[str, Any]:
    """Compare wire observations with the run traces, per run, keyed by wire_id.

    ``steplog_path`` is read together with its rotated generation (``<path>.1``) when one exists.
    ``run_id`` restricts both files to one run: the per-run reconciliation a shared wire log needs.
    """
    wire_records = _read_jsonl(wire_path)
    rows = _read_trace_rows(steplog_path)
    if run_id is not None:
        wire_records = [row for row in wire_records if row.get("run_id") == run_id]
        rows = [row for row in rows if str(row.get("run_id") or "") == run_id]
    traces = [(str(row.get("run_id") or ""), row) for row in rows]
    traced_ids = {rid for rid, _ in traces if rid}
    step_records = [(rid, step) for rid, row in traces for step in _steps_of(row)]
    wire_by_id: dict[str, dict[str, Any]] = {}
    for row in wire_records:
        if row.get("wire_id"):
            wire_by_id.setdefault(str(row["wire_id"]), row)
    steps_by_id = {str(step["wire_id"]): (rid, step) for rid, step in step_records if step.get("wire_id")}
    audit = _Audit()
    _check_steps(step_records, wire_by_id, audit)
    claims = {str(call["wire_id"]): (rid, row, call) for rid, row in traces for call in _side_calls(row)
              if call.get("wire_id")}
    for wire_id, (rid, _row, _call) in sorted(claims.items()):
        if wire_id not in wire_by_id:
            audit.add("side_calls_without_wire", wire_id, rid)
    oldest = min((str(row["ts"]) for _, row in traces if row.get("ts")), default="")
    groups = _classify_records(wire_records, wire_by_id, set(steps_by_id), traced_ids, claims, oldest, audit)
    duplicates = len(wire_records) - len(wire_by_id) + len(step_records) - len(steps_by_id)
    found = audit.found
    legacy = groups["by_kind"].get(UNKNOWN, 0)
    result: dict[str, Any] = {
        "wire_records": len(wire_records),
        "steplog_records": len(step_records),
        "missing_steplog": sorted(found["missing_steplog"]),
        "missing_wire": found["missing_wire"],
        "altered": sorted(found["altered"]),
        "duplicate_ids": duplicates,
        "kind_mismatch": sorted(found["kind_mismatch"]),
        "run_mismatch": sorted(found["run_mismatch"]),
        "unaccounted": sorted(found["unaccounted"]),
        "implausible": found["implausible"],
        "side_calls_without_wire": found["side_calls_without_wire"],
        **groups,
        "legacy_records": legacy,
        "per_run": _per_run(traces, wire_records, audit),
    }
    if legacy:
        result["note"] = (f"{legacy} wire record(s) carry no kind (written before S30-61 Amendment 5): "
                          "read as 'unknown' and compared as steps, as before")
    result["clean"] = not (
        result["missing_steplog"] or result["missing_wire"] or result["altered"] or duplicates
        or result["kind_mismatch"] or result["run_mismatch"] or result["unaccounted"]
        or result["implausible"] or result["side_calls_without_wire"] or result["missing_runs"]
    )
    return result


def _classify_records(
    wire_records: list[dict[str, Any]],
    wire_by_id: dict[str, dict[str, Any]],
    step_ids: set[str],
    traced_ids: set[str],
    claims: dict[str, tuple[str, dict[str, Any], dict[str, Any]]],
    oldest: str,
    audit: _Audit,
) -> dict[str, Any]:
    """Every wire record no step points at: a discrepancy, a claimed call, or a reported group."""
    missing_runs: dict[str, Counter[str]] = defaultdict(Counter)
    before_window: dict[str, Counter[str]] = defaultdict(Counter)
    untraced_runs: dict[str, Counter[str]] = defaultdict(Counter)
    outside_runs: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    newest: dict[str, str] = {}
    for record in wire_records:
        key = str(record.get("run_id"))
        newest[key] = max(newest.get(key, ""), str(record.get("ts") or ""))
    for wire_id, record in sorted(wire_by_id.items()):
        kind, owner = record.get("kind"), record.get("run_id")
        by_kind[str(kind) if kind is not None else UNKNOWN] += 1
        if wire_id in step_ids:
            continue  # compared in `_check_steps`
        if kind is None or owner is None:
            if kind is None or kind == STEP:
                # A record from before kinds existed, or a "step" no run owns: read as before.
                audit.add("missing_steplog", wire_id, "")
            else:
                outside_runs[str(kind)] += 1
            continue
        run = str(owner)
        if run in traced_ids:
            if kind == STEP:
                audit.add("missing_steplog", wire_id, run)
            else:
                _check_claim(wire_id, record, claims.get(wire_id), run, audit)
        elif not record.get("traced"):
            untraced_runs[run][str(kind)] += 1
        elif oldest and newest.get(run, "") < oldest:
            # Every record of the run predates the oldest trace line still on disk: the trace keeps
            # one rotated generation, so this run's line rotated out. Reported, not a discrepancy.
            before_window[run][str(kind)] += 1
        else:
            # A trace was promised and is absent: the run raised, or its line was removed.
            missing_runs[run][str(kind)] += 1

    def table(groups: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
        return {run: dict(kinds) for run, kinds in sorted(groups.items())}

    return {
        "missing_runs": table(missing_runs),
        "before_trace_window": table(before_window),
        "untraced_runs": table(untraced_runs),
        "outside_runs": dict(sorted(outside_runs.items())),
        "by_kind": dict(sorted(by_kind.items())),
    }


def _check_steps(
    step_records: list[tuple[str, dict[str, Any]]], wire_by_id: dict[str, dict[str, Any]], audit: _Audit
) -> None:
    """Every step must point at a ``step`` record of its own run with the digests it copied."""
    for rid, step in sorted(step_records, key=lambda item: str(item[1].get("wire_id") or "")):
        wire_id = str(step.get("wire_id") or "")
        if not wire_id:
            audit.add("missing_wire", f"step:{step.get('index', '?')}", rid)
            continue
        wire = wire_by_id.get(wire_id)
        if wire is None:
            audit.add("missing_wire", wire_id, rid)
            continue
        if any(wire.get(f) != step.get(f) for f in ("request_digest", "response_digest")):
            audit.add("altered", wire_id, rid)
        if wire.get("kind") is not None and wire.get("kind") != STEP:
            # A step the trace keeps, witnessed only by a record of another kind: the shape a forged
            # summary record covering a fabricated step would take.
            audit.add("kind_mismatch", wire_id, rid)
        if rid and wire.get("run_id") is not None and wire.get("run_id") != rid:
            audit.add("run_mismatch", wire_id, rid)


def _check_claim(
    wire_id: str, record: dict[str, Any], claim: tuple[str, dict[str, Any], dict[str, Any]] | None,
    rid: str, audit: _Audit,
) -> None:
    """A non-step record of a traced run: claimed by that run's trace, and plausible there."""
    kind = str(record.get("kind"))
    if claim is None or claim[0] != rid or str(claim[2].get("kind")) != kind or any(
        claim[2].get(f) != record.get(f) for f in ("request_digest", "response_digest")
    ):
        audit.add("unaccounted", wire_id, rid)
        return
    _, row, call = claim
    reason = _implausible(kind, row, call)
    if reason:
        audit.add("implausible", {"wire_id": wire_id, "kind": kind, "reason": reason}, rid)


def _implausible(kind: str, row: dict[str, Any], call: dict[str, Any]) -> str:
    """Why a claimed call does not fit the run's own trace, or ""."""
    at = call.get("at_step")
    step = next((s for s in _steps_of(row) if s.get("index") == at), None)
    if kind == SUMMARY and not (step is not None and step.get("compacted")):
        return f"a summary call at step {at}, which did not compact"
    if kind == CLOSE and row.get("stopped_reason") not in _CLOSING_STOPS:
        return f"a closing call in a run that stopped {row.get('stopped_reason')!r}"
    if kind == EMPTY_RETRY and not (step is not None and not step.get("tools")):
        return f"an empty-reply retry at step {at}, which called a tool"
    return ""


def _per_run(
    traces: list[tuple[str, dict[str, Any]]], wire_records: list[dict[str, Any]], audit: _Audit
) -> dict[str, Any]:
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    for record in wire_records:
        if record.get("run_id"):
            kinds[str(record["run_id"])][str(record.get("kind") or UNKNOWN)] += 1
    return {
        rid: {
            "stopped_reason": row.get("stopped_reason"),
            "steps": len(_steps_of(row)),
            "wire_by_kind": dict(sorted(kinds[rid].items())),
            "discrepancies": audit.by_run[rid],
            "clean": audit.by_run[rid] == 0,
        }
        for rid, row in traces
        if rid
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number}: expected a JSON object")
        rows.append(value)
    return rows


def _read_trace_rows(path: Path) -> list[dict[str, Any]]:
    """The trace lines, the rotated generation first (``StepLog`` keeps exactly one)."""
    rotated = path.with_name(path.name + ".1")
    return [*_read_jsonl(rotated), *_read_jsonl(path)]


def _steps_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    steps = row.get("steps")
    if isinstance(steps, list):
        return [step for step in steps if isinstance(step, dict)]
    return [row] if "wire_id" in row else []


def _side_calls(row: dict[str, Any]) -> list[dict[str, Any]]:
    calls = row.get("side_calls")
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
