"""Append-only provider observations and comparison with harness steplogs."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def digest(value: Any) -> str:
    """Return a stable digest without retaining the value being fingerprinted."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def append_wire_record(path: Path, *, model: str, request_digest: str, response_digest: str) -> str:
    """Append one metadata-only event; the payload and credentials never reach disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record_id = uuid.uuid4().hex
    line = json.dumps(
        {
            "wire_id": record_id,
            "model": model,
            "request_digest": request_digest,
            "response_digest": response_digest,
            "ts": datetime.now(UTC).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    with _LOCK:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)
    return record_id


def reconcile(wire_path: Path, steplog_path: Path) -> dict[str, Any]:
    """Compare wire observations with JSONL step records, keyed by wire_id."""
    wire_records = _read_jsonl(wire_path)
    step_records = _read_steplog(steplog_path)
    wire_by_id = {str(row["wire_id"]): row for row in wire_records if row.get("wire_id")}
    steps_by_id = {str(row["wire_id"]): row for row in step_records if row.get("wire_id")}
    missing_steps = sorted(set(wire_by_id) - set(steps_by_id))
    missing_wire = sorted(
        f"step:{row.get('index', '?')}" for row in step_records if not row.get("wire_id")
    )
    missing_wire.extend(sorted(set(steps_by_id) - set(wire_by_id)))
    altered: list[str] = []
    for identifier in sorted(set(wire_by_id) & set(steps_by_id)):
        wire, step = wire_by_id[identifier], steps_by_id[identifier]
        if any(
            wire.get(field) != step.get(field)
            for field in ("request_digest", "response_digest")
        ):
            altered.append(identifier)
    duplicates = len(wire_records) - len(wire_by_id) + len(step_records) - len(steps_by_id)
    return {
        "wire_records": len(wire_records),
        "steplog_records": len(step_records),
        "missing_steplog": missing_steps,
        "missing_wire": missing_wire,
        "altered": altered,
        "duplicate_ids": duplicates,
        "clean": not (missing_steps or missing_wire or altered or duplicates),
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


def _read_steplog(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in _read_jsonl(path):
        steps = row.get("steps")
        if isinstance(steps, list):
            result.extend(step for step in steps if isinstance(step, dict))
        elif "wire_id" in row:
            result.append(row)
    return result
