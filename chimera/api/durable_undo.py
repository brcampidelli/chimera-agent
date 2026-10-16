"""Durable, turn-scoped undo for the Code conversation surface.

The old receipt token held a ``WorkspaceGuard`` and a ``TurnChange`` only in process memory. A restart
therefore made every button stale, and retaining a bounded list of tokens could never answer "go back
to turn one". This store persists only changed file states, outside the project tree, and reuses the
checkpoint's per-file compare-before-restore rule so a later human edit wins over an undo request.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from chimera.core.checkpoint import FileSnapshot, TurnChange, WorkspaceGuard

#: A project can consume at most this much durable undo storage. Trimming is oldest-turn-first.
MAX_PROJECT_BYTES = 50 * 1024 * 1024


def _state_dict(state: tuple[bool, str | None]) -> dict[str, Any]:
    return {"present": state[0], "text": state[1]}


def _state(raw: dict[str, Any]) -> tuple[bool, str | None]:
    return bool(raw.get("present")), raw.get("text") if isinstance(raw.get("text"), str) else None


def _change_dict(change: TurnChange) -> dict[str, Any]:
    return {
        "before": {
            "present": sorted(change.before.present),
            "files": change.before.files,
        },
        "after": {path: _state_dict(state) for path, state in change.after.items()},
        "truncated": change.truncated,
    }


def _change(raw: dict[str, Any]) -> TurnChange:
    before = raw.get("before", {})
    after = raw.get("after", {})
    if not isinstance(before, dict):
        before = {}
    if not isinstance(after, dict):
        after = {}
    files = before.get("files", {})
    if not isinstance(files, dict):
        files = {}
    present = before.get("present", [])
    if not isinstance(present, list):
        present = []
    return TurnChange(
        before=FileSnapshot(
            files={k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)},
            present={p for p in present if isinstance(p, str) and _safe_relative(p)},
        ),
        after={k: _state(v) for k, v in after.items()
               if isinstance(k, str) and isinstance(v, dict) and _safe_relative(k)},
        truncated=bool(raw.get("truncated")),
    )


def _safe_relative(value: str) -> bool:
    """Reject corrupt manifests that could escape the recorded workspace during restore."""
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    return (
        "\\" not in value
        and not path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and bool(path.parts)
        and all(part not in ("..", "") for part in path.parts)
    )


class DurableUndo:
    """Persist Code turns under ``CHIMERA_HOME/snapshots/<project-hash>``.

    The project directory is an opaque hash, never a path derived from a client-supplied identifier.
    The workspace is stored as a resolved path for replay, but no files outside the changed-path
    manifest are read or written by rollback. A lock serializes metadata updates within this process.
    """

    def __init__(self, home: Path, *, max_bytes: int = MAX_PROJECT_BYTES) -> None:
        self.root = Path(home) / "snapshots"
        self.max_bytes = max_bytes
        self._lock = threading.RLock()

    def _project_dir(self, workspace: Path) -> Path:
        resolved = Path(workspace).resolve()
        digest = hashlib.sha256(os.fsencode(str(resolved))).hexdigest()
        return self.root / digest

    def _read(self, directory: Path) -> dict[str, Any]:
        path = directory / "turns.json"
        if not path.is_file():
            return {"turns": [], "redo": None}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"turns": [], "redo": None}
        if isinstance(raw, dict) and isinstance(raw.get("turns"), list):
            return {"turns": raw["turns"], "redo": raw.get("redo"), "cutoff": raw.get("cutoff")}
        return {"turns": [], "redo": None}

    def _write(self, directory: Path, data: dict[str, Any]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._trim(directory, data)

    def _trim(self, directory: Path, data: dict[str, Any]) -> None:
        """Retain newest snapshots within the project's disk budget and persist the cutoff."""
        turns = data["turns"]
        while turns:
            encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) <= self.max_bytes:
                break
            removed = turns.pop(0)
            data["cutoff"] = removed.get("turn_id") if isinstance(removed, dict) else None
            data["redo"] = None
        if len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > self.max_bytes:
            # Redo contains another copy of pre-revert files; drop it if the retained state exceeds cap.
            data["redo"] = None
        path = directory / "turns.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)

    def invalidate_redo(self, session_id: str, workspace: Path) -> None:
        """A fresh turn consumes the redo window even if it makes no file changes."""
        with self._lock:
            directory = self._project_dir(workspace)
            data = self._read(directory)
            redo = data.get("redo")
            if (
                isinstance(redo, dict)
                and redo.get("session_id") == session_id
                and redo.get("workspace") == str(Path(workspace).resolve())
            ):
                data["redo"] = None
                self._write(directory, data)

    def record(self, session_id: str, turn_id: str, workspace: Path, change: TurnChange) -> None:
        """Append the changed-file snapshot, invalidating redo after any new editing turn."""
        if not change.after:
            self.invalidate_redo(session_id, workspace)
            return
        with self._lock:
            directory = self._project_dir(workspace)
            data = self._read(directory)
            data["redo"] = None
            # Keep a single global chronological list; the session id scopes list/revert operations.
            turns = data["turns"]
            turns.append({
                "session_id": session_id,
                "turn_id": turn_id,
                "workspace": str(Path(workspace).resolve()),
                "created_at": time.time(),
                "change": _change_dict(change),
            })
            data["turns"] = turns
            data["redo"] = None
            self._write(directory, data)

    def list_turns(self, session_id: str, workspace: Path) -> list[dict[str, Any]]:
        """Return this conversation's retained turns and changed paths, oldest first."""
        with self._lock:
            turns = self._read(self._project_dir(workspace))["turns"]
            return [
                {
                    "turn_id": item["turn_id"],
                    "created_at": item["created_at"],
                    "files": sorted(_change(item["change"]).paths),
                }
                for item in turns
                if isinstance(item, dict)
                and isinstance(item.get("turn_id"), str)
                and isinstance(item.get("created_at"), (int, float))
                and item.get("session_id") == session_id
                and isinstance(item.get("change"), dict)
            ]

    def cutoff(self, workspace: Path) -> str | None:
        """Which oldest snapshot was removed to fit the declared per-project disk budget."""
        with self._lock:
            value = self._read(self._project_dir(workspace)).get("cutoff")
            return str(value) if value else None

    def revert_to(self, session_id: str, turn_id: str, workspace: Path) -> dict[str, Any]:
        """Undo every later retained turn, newest first; the selected turn itself remains applied."""
        with self._lock:
            directory = self._project_dir(workspace)
            data = self._read(directory)
            selected = [
                i for i, item in enumerate(data["turns"])
                if isinstance(item, dict)
                and isinstance(item.get("change"), dict)
                and item.get("session_id") == session_id
                and item.get("turn_id") == turn_id
            ]
            if not selected:
                return {"ok": False, "reason": "turn is not retained"}
            start = selected[-1]
            later_items = data["turns"][start + 1:]
            targets = [
                item for item in later_items
                if isinstance(item, dict)
                and isinstance(item.get("turn_id"), str)
                and isinstance(item.get("change"), dict)
                and item.get("session_id") == session_id
            ]
            if not targets:
                data["redo"] = None
                self._write(directory, data)
                return {"ok": True, "restored": 0, "kept": [], "left_new_files": [],
                        "reverted_turns": [], "cutoff": data.get("cutoff")}
            workspace_path = Path(workspace).resolve()
            if not workspace_path.is_dir():
                return {"ok": False, "reason": "workspace no longer exists"}
            guard = WorkspaceGuard(workspace_path)
            before_redo: dict[str, tuple[bool, str | None]] = {}
            range_turn_ids: list[str] = []
            eligible_turn_ids = {id(item) for item in targets}
            kept: set[str] = set()
            left_new: set[str] = set()
            restored = 0
            blocked: set[str] = set()
            for item in reversed(targets):
                raw_change = item.get("change")
                if not isinstance(raw_change, dict):
                    continue
                change = _change(raw_change)
                range_turn_ids.append(str(item["turn_id"]))
                for rel in change.paths:
                    try:
                        self._validate_target(guard.workspace, rel)
                    except (OSError, ValueError):
                        kept.add(rel)
                        blocked.add(rel)
                        continue
                    # Redo must restore the state at the start of the entire rollback, not the
                    # intermediate state seen while unwinding a chain of edits to this path.
                    before_redo.setdefault(rel, guard._state(rel))
                applicable = TurnChange(
                    before=change.before,
                    after={rel: state for rel, state in change.after.items() if rel not in blocked},
                    truncated=change.truncated,
                )
                report = self._restore_exact(guard, applicable)
                restored += report[0]
                kept.update(report[1])
                blocked.update(report[1])
                left_new.update(report[2])
            expected = {
                rel: guard._state(rel)
                for rel in before_redo
                if rel not in kept and rel not in left_new and rel not in blocked
            }
            data["redo"] = {
                "session_id": session_id,
                "workspace": str(workspace_path),
                "restore": {
                    rel: _state_dict(state) for rel, state in before_redo.items() if rel in expected
                },
                "expected": {rel: _state_dict(state) for rel, state in expected.items()},
            }
            data["turns"] = [
                item for item in data["turns"]
                if not (
                    isinstance(item, dict)
                    and item.get("session_id") == session_id
                    and id(item) in eligible_turn_ids
                )
            ]
            # Removed turns are the only place their before-images live. They stay available in
            # redo, but must count against the same project storage ceiling as retained snapshots.
            encoded_size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            while data["turns"] and encoded_size > self.max_bytes:
                removed = data["turns"].pop(0)
                data["cutoff"] = removed.get("turn_id") if isinstance(removed, dict) else None
                data["redo"] = None
                encoded_size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self._write(directory, data)
            return {"ok": True, "restored": restored, "kept": sorted(kept),
                    "left_new_files": sorted(left_new), "reverted_turns": range_turn_ids,
                    "cutoff": data.get("cutoff")}

    @staticmethod
    def _validate_target(workspace: Path, rel: str) -> Path:
        """Reject symlink traversal even when its current target remains inside the workspace."""
        if not _safe_relative(rel):
            raise ValueError("unsafe relative path")
        target = workspace / rel
        target.resolve(strict=False).relative_to(workspace)
        if target.is_symlink():
            raise ValueError("symlink target")
        parent = target.parent
        while parent != workspace:
            if parent.is_symlink():
                raise ValueError("symlinked parent")
            parent = parent.parent
        return target

    @staticmethod
    def _restore_exact(
        guard: WorkspaceGuard, change: TurnChange
    ) -> tuple[int, list[str], list[str]]:
        """Restore only manifest paths whose current value still equals this turn's output.

        Unlike a workspace-wide cleanup, deleting a captured path created by the turn is bounded to
        that exact path and compare-checked. The checkpoint's broad delete prohibition inside git
        remains intact; this narrower operation is safe because no unlisted repository file can be
        considered and a concurrent replacement is preserved.
        """
        restored = 0
        kept: list[str] = []
        left_new: list[str] = []
        for rel in change.paths:
            if not _safe_relative(rel):
                kept.append(rel)
                continue
            try:
                target = DurableUndo._validate_target(guard.workspace, rel)
            except (OSError, ValueError):
                kept.append(rel)
                continue
            if guard._state(rel) != change.after[rel]:
                kept.append(rel)
                continue
            if change.truncated and rel not in change.before.present:
                left_new.append(rel)
                continue
            if rel in change.before.present:
                text = change.before.files.get(rel)
                if text is None:
                    kept.append(rel)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                restored += 1
            else:
                target.unlink(missing_ok=True)
                restored += 1
        return restored, kept, left_new

    def redo(self, session_id: str, workspace: Path) -> dict[str, Any]:
        """Restore the state before the last range undo, but only where no one edited afterward."""
        with self._lock:
            directory = self._project_dir(workspace)
            data = self._read(directory)
            redo = data.get("redo")
            if (
                not isinstance(redo, dict)
                or redo.get("session_id") != session_id
                or redo.get("workspace") != str(Path(workspace).resolve())
            ):
                return {"ok": False, "reason": "nothing to redo"}
            workspace_path = Path(workspace).resolve()
            if not workspace_path.is_dir():
                data["redo"] = None
                self._write(directory, data)
                return {"ok": False, "reason": "workspace no longer exists"}
            guard = WorkspaceGuard(workspace_path)
            restored = 0
            kept: list[str] = []
            restore = redo.get("restore", {})
            expected = redo.get("expected", {})
            if not isinstance(restore, dict) or not isinstance(expected, dict):
                data["redo"] = None
                self._write(directory, data)
                return {"ok": False, "reason": "invalid redo snapshot"}
            for rel, raw_before in restore.items():
                if not isinstance(rel, str) or not _safe_relative(rel) or not isinstance(raw_before, dict):
                    continue
                expected_raw = expected.get(rel)
                if not isinstance(expected_raw, dict):
                    kept.append(rel)
                    continue
                before_state = _state(raw_before)
                current = _state(expected_raw)
                try:
                    self._validate_target(guard.workspace, rel)
                except (OSError, ValueError):
                    kept.append(rel)
                    continue
                snap = FileSnapshot(
                    files={rel: before_state[1]} if before_state[0] and before_state[1] is not None else {},
                    present={rel} if before_state[0] else set(),
                )
                change = TurnChange(before=snap, after={rel: current})
                # restore_change's compare is against the current state; use its own safety rule.
                outcome = self._restore_exact(guard, change)
                restored += outcome[0]
                kept.extend(outcome[1])
            data["redo"] = None
            self._write(directory, data)
            return {"ok": True, "restored": restored, "kept": sorted(set(kept))}

    def forget_session(self, session_id: str) -> int:
        """Delete retained snapshots for a deleted conversation from every project bucket."""
        removed = 0
        with self._lock:
            if not self.root.exists():
                return 0
            for directory in self.root.iterdir():
                data = self._read(directory)
                before = len(data["turns"])
                data["turns"] = [item for item in data["turns"]
                                 if not isinstance(item, dict) or item.get("session_id") != session_id]
                redo_removed = (
                    isinstance(data.get("redo"), dict)
                    and data["redo"].get("session_id") == session_id
                )
                if redo_removed:
                    data["redo"] = None
                removed += before - len(data["turns"])
                if before != len(data["turns"]) or redo_removed:
                    if data["turns"] or data.get("redo") is not None:
                        self._write(directory, data)
                    else:
                        (directory / "turns.json").unlink(missing_ok=True)
                        with contextlib.suppress(OSError):  # not empty: something else lives there
                            directory.rmdir()
        return removed
