"""Declared write-region — a capability boundary for the file-writing tools (M18-3).

The workspace jail (:func:`~chimera.tools.workspace.resolve_in_workspace`) already blocks writes
*outside* the workspace. But within it, nothing stops a run from touching an unrelated file — which
is exactly the injection→arbitrary-write attack: a hostile page tells the agent to "also update
``config/secrets.py``" and the agent, following the DATA as instructions, rewrites a file the task
never mentioned. The taint ledger *escalates* such a write to review; the write-region *refuses* it
outright (fail-closed).

Inspired by PatchOptic (arXiv 2607.05483): a step declares which paths it may write, and a write
outside that declaration is rejected before it touches disk. Opt-in — an empty region allows every
in-workspace path (today's behaviour), so nothing changes until a region is declared.

Patterns are globs relative to the workspace, matched permissively (``fnmatch``: ``*`` spans ``/``),
so ``src/**`` or ``*.py`` cover nested files. A path resolving outside the workspace is always denied.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


class WriteRegion:
    """An allowlist of workspace-relative globs the file-writers may touch."""

    def __init__(self, patterns: list[str], workspace: Path) -> None:
        self.patterns = [p.strip().replace("\\", "/") for p in patterns if p.strip()]
        self.workspace = Path(workspace).resolve()

    def _rel(self, path: Path) -> str | None:
        try:
            return Path(path).resolve().relative_to(self.workspace).as_posix()
        except ValueError:
            return None  # outside the workspace entirely

    def allows(self, path: Path) -> bool:
        """True if writing ``path`` is permitted (an empty region permits any in-workspace path)."""
        rel = self._rel(path)
        if rel is None:
            return False
        if not self.patterns:
            return True
        return any(fnmatch.fnmatch(rel, pat) for pat in self.patterns)

    def check(self, path: Path) -> str | None:
        """Return an error string if the write is disallowed, else None."""
        if self.allows(path):
            return None
        rel = self._rel(path)
        target = rel if rel is not None else str(path)
        return (
            f"error: write to {target!r} is outside the declared write-region "
            f"({', '.join(self.patterns) or 'workspace'}) — refused"
        )
