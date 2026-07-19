"""Workspace snapshot/restore — the mechanism behind verify-or-revert.

Before an autonomous attempt, take a snapshot; if verification fails, restore it.
This is a text-file checkpoint (dependency-free, fully testable): it records the
contents of text files and the set of all files present, then on restore deletes
files created since, rewrites changed ones, and recreates deleted ones.

Binary files are tracked for presence (so they are not deleted) but their contents
are not snapshotted. Large files and common build/VCS dirs are skipped.

The "delete files created since" pass is destructive, so it is skipped whenever it
cannot be done safely: when the snapshot was truncated at the file cap, and when the
workspace is a real git repository root (verify-or-revert must never delete a repo's
files — a task workspace is a throwaway dir, never a repo root).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from chimera.telemetry import get_logger

_log = get_logger("core.checkpoint")

_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".chimera",
}
_MAX_FILE_BYTES = 1_000_000


@dataclass
class FileSnapshot:
    """A point-in-time capture of a workspace's text files."""

    files: dict[str, str] = field(default_factory=dict)
    present: set[str] = field(default_factory=set)


class WorkspaceGuard:
    """Snapshots and restores a workspace directory."""

    def __init__(self, workspace: Path, *, max_files: int = 5000) -> None:
        self.workspace = Path(workspace).resolve()
        self.max_files = max_files

    def _iter_files(self) -> Iterator[Path]:
        for path in self.workspace.rglob("*"):
            if path.is_dir():
                continue
            rel_parts = path.relative_to(self.workspace).parts
            if any(part in _IGNORE_DIRS for part in rel_parts):
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path

    def snapshot(self) -> FileSnapshot:
        snap = FileSnapshot()
        for path in self._iter_files():
            rel = path.relative_to(self.workspace).as_posix()
            snap.present.add(rel)
            try:
                snap.files[rel] = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary/unreadable: presence tracked, content skipped
            if len(snap.present) >= self.max_files:
                break
        return snap

    def restore(self, snapshot: FileSnapshot) -> int:
        """Restore the workspace to ``snapshot``. Returns the number of changes."""
        changes = 0
        # Two conditions make the destructive delete-new pass unsafe; in either case we skip it and
        # only restore the content we captured (never worse than the pre-restore state):
        #
        #  1. TRUNCATED snapshot (hit the file cap): some pre-existing files were never captured, so we
        #     cannot tell "created since" from "existed but uncaptured". Deleting the difference would
        #     destroy untouched user data — the opposite of verify-or-revert's job.
        #  2. The workspace is INSIDE A GIT REPOSITORY (a `.git` entry sits at it or any ancestor).
        #     verify-or-revert's delete pass exists to clean up files an agent CREATED in a throwaway
        #     workspace; a task workspace is never inside a repo. If a path/config bug ever points the
        #     guard at a real repo (or a SUBDIR of one), "files not in my snapshot" spans the user's
        #     untracked work and — if the snapshot predates them — committed files, so a revert could
        #     wipe the repo. It has (bench harness, 2026-07-17). Refuse to delete anywhere inside a repo,
        #     unconditionally — checking ancestors too, not just the immediate directory.
        truncated = len(snapshot.present) >= self.max_files
        in_git_repo = any(
            (parent / ".git").exists() for parent in (self.workspace, *self.workspace.parents)
        )
        if truncated:
            _log.warning(
                "snapshot truncated at %d files; skipping delete-new pass on restore to avoid "
                "removing uncaptured pre-existing files", self.max_files,
            )
        elif in_git_repo:
            _log.warning(
                "workspace %s is inside a git repository; skipping delete-new pass on restore so "
                "verify-or-revert can never delete tracked or untracked repo files", self.workspace,
            )
        else:
            current = {p.relative_to(self.workspace).as_posix() for p in self._iter_files()}
            for rel in current - snapshot.present:
                (self.workspace / rel).unlink(missing_ok=True)
                changes += 1

        for rel, content in snapshot.files.items():
            target = self.workspace / rel
            if not target.exists() or target.read_text(encoding="utf-8", errors="replace") != content:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                changes += 1

        if changes:
            _log.debug("restored workspace (%d changes)", changes)
        return changes
