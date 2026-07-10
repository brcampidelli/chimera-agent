"""Surgical file-editing tools: exact-match replacement instead of whole-file rewrites.

The agent's only writer used to be ``write_file``, which rewrites the entire file — it burns
tokens on large files and risks clobbering unrelated content when the model reconstructs the
whole thing from memory. These tools edit *in place*:

- ``edit_file`` replaces one exact ``old`` string with ``new`` (the workhorse for a single edit).
- ``apply_patch`` applies several search/replace hunks to one file atomically (multi-edit).

Both are anchored on an **exact, unique** match: an ``old``/search that is missing (0 matches)
or ambiguous (>1 match, without ``replace_all``) is refused rather than guessed — a wrong-place
edit is worse than no edit. ``apply_patch`` is all-or-nothing: if any hunk fails to anchor, the
file is left untouched and the failing hunk is named.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.workspace import resolve_in_workspace
from chimera.tools.write_region import WriteRegion

# Conflict-marker hunk format, familiar to models from git and Aider:
#   <<<<<<< SEARCH
#   old text
#   =======
#   new text
#   >>>>>>> REPLACE
_HUNK_OPEN = "<<<<<<< SEARCH"
_HUNK_MID = "======="
_HUNK_CLOSE = ">>>>>>> REPLACE"


class _WorkspaceTool(Tool):
    """Base for tools bound to a workspace root (with an optional declared write-region)."""

    def __init__(self, workspace: Path | None = None, *, write_region: WriteRegion | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.write_region = write_region


class EditFileTool(_WorkspaceTool):
    name = "edit_file"
    description = (
        "Replace an exact substring in a workspace file (surgical edit — prefer this over "
        "write_file for changing an existing file). 'old' must match exactly and, unless "
        "replace_all is true, appear exactly once; a missing or ambiguous match is refused."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "old": {"type": "string", "description": "Exact text to find (include enough context to be unique)."},
            "new": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
        },
        "required": ["path", "old", "new"],
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if self.write_region is not None and (err := self.write_region.check(path)):
            return err
        old = str(kwargs["old"])
        new = str(kwargs["new"])
        replace_all = bool(kwargs.get("replace_all", False))
        rel = kwargs["path"]
        if not path.is_file():
            return f"error: file not found: {rel}"
        if old == "":
            return "error: 'old' must be non-empty (it anchors the edit)"
        if old == new:
            return "error: 'old' and 'new' are identical — nothing to change"
        content = path.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            return f"error: 'old' not found in {rel} (must match exactly, including whitespace)"
        if count > 1 and not replace_all:
            return (
                f"error: 'old' appears {count} times in {rel} — add surrounding context to make "
                "it unique, or pass replace_all=true"
            )
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8")
        where = f"{count} occurrences" if replace_all else "1 occurrence"
        return f"edited {rel}: replaced {where}"


def _parse_hunks(patch: str) -> list[tuple[str, str]]:
    """Parse conflict-marker hunks into (search, replace) pairs. Raises ValueError on malformed."""
    hunks: list[tuple[str, str]] = []
    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() != _HUNK_OPEN:
            if lines[i].strip():
                raise ValueError(f"expected {_HUNK_OPEN!r}, got {lines[i]!r}")
            i += 1
            continue
        try:
            mid = lines.index(_HUNK_MID, i + 1)
            close = lines.index(_HUNK_CLOSE, mid + 1)
        except ValueError as exc:
            raise ValueError("unterminated hunk: expected '=======' then '>>>>>>> REPLACE'") from exc
        search = "\n".join(lines[i + 1 : mid])
        replace = "\n".join(lines[mid + 1 : close])
        hunks.append((search, replace))
        i = close + 1
    if not hunks:
        raise ValueError("no hunks found (expected '<<<<<<< SEARCH / ======= / >>>>>>> REPLACE' blocks)")
    return hunks


class ApplyPatchTool(_WorkspaceTool):
    name = "apply_patch"
    description = (
        "Apply multiple search/replace hunks to one workspace file, atomically. The patch is a "
        "sequence of '<<<<<<< SEARCH / ======= / >>>>>>> REPLACE' blocks; each SEARCH must match "
        "exactly once. If any hunk fails to anchor, the file is left unchanged."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "patch": {
                "type": "string",
                "description": "One or more '<<<<<<< SEARCH / ======= / >>>>>>> REPLACE' hunks.",
            },
        },
        "required": ["path", "patch"],
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if self.write_region is not None and (err := self.write_region.check(path)):
            return err
        rel = kwargs["path"]
        if not path.is_file():
            return f"error: file not found: {rel}"
        try:
            hunks = _parse_hunks(str(kwargs["patch"]))
        except ValueError as exc:
            return f"error: {exc}"
        content = path.read_text(encoding="utf-8")
        # Apply against a working copy; only persist if every hunk anchors uniquely (atomic).
        working = content
        for index, (search, replace) in enumerate(hunks, start=1):
            if search == "":
                return f"error: hunk {index} has an empty SEARCH block (it anchors the edit)"
            occurrences = working.count(search)
            if occurrences == 0:
                return f"error: hunk {index} SEARCH not found (must match exactly, incl. whitespace)"
            if occurrences > 1:
                return f"error: hunk {index} SEARCH is ambiguous ({occurrences} matches) — add context"
            working = working.replace(search, replace, 1)
        path.write_text(working, encoding="utf-8")
        return f"applied {len(hunks)} hunk(s) to {rel}"
