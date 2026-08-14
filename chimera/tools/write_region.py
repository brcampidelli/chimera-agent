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

One thing is denied even with no region declared, and cannot be allowed by declaring one: the
machinery the workspace is governed BY. See :data:`ALWAYS_DENIED`.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

#: Paths no run may write, region or no region — and no declared region can grant them.
#:
#: The difference this draws is between damaging a file and damaging the ability to undo the damage.
#: Every safety property this project offers downstream of a write — revert, checkpoint, the diff
#: gate, "the workspace is a git checkout so nothing is lost" — is a property of `.git`. A run that
#: rewrites history has not made a mistake that can be reviewed; it has removed the reviewer. The
#: same is true of `.chimera`, which holds the audit log, the taint ledger's records and the run
#: receipts: a run that edits its own evidence is the one case where a receipt proves nothing.
#:
#: It is a short list on purpose. A long denylist becomes a thing people work around; these three
#: are the ones whose loss is not recoverable from inside the system that lost them. Codex CLI
#: reaches the same conclusion about `.git` from the other direction — it marks it read-only inside
#: the writable root — which is some evidence the boundary is in the right place.
ALWAYS_DENIED: tuple[str, ...] = (".git", ".chimera", ".env")


def _under(rel: str, directory: str) -> bool:
    """True when ``rel`` IS ``directory`` or lives inside it."""
    return rel == directory or rel.startswith(f"{directory}/")


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
        """True if writing ``path`` is permitted (an empty region permits any in-workspace path).

        :data:`ALWAYS_DENIED` is checked FIRST, so it survives both the empty-region default and any
        pattern a caller declares. A denylist a declaration can override is a suggestion.
        """
        rel = self._rel(path)
        if rel is None:
            return False
        if any(_under(rel, denied) for denied in ALWAYS_DENIED):
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


def refuse_write(workspace: Path, path: Path, region: WriteRegion | None) -> str | None:
    """The single gate every file-writing tool passes. Returns an error string, or None to allow.

    Exists because the never-writable set has to apply when NO region was declared, which is the
    default and therefore the case that matters. The writers used to read ``if region is not None``,
    so a denylist living only inside :class:`WriteRegion` would have been unreachable in exactly the
    configuration everybody runs — code that looks like a guard and never executes.
    """
    rel = None
    try:
        rel = Path(path).resolve().relative_to(Path(workspace).resolve()).as_posix()
    except ValueError:
        rel = None
    if rel is not None and any(_under(rel, denied) for denied in ALWAYS_DENIED):
        return (
            f"error: {rel!r} is never writable — it holds the history and the receipts this run "
            "would be reviewed by. Refused."
        )
    return region.check(path) if region is not None else None
