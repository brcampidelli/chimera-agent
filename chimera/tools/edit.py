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
from chimera.tools.workspace import atomic_write_text, read_text_for_edit, resolve_in_workspace
from chimera.tools.write_region import WriteRegion, refuse_write

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
        if err := refuse_write(self.workspace, path, self.write_region):
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
        try:
            content, newline = read_text_for_edit(path)
        except UnicodeDecodeError:
            return f"error: {rel} is not a UTF-8 text file — cannot edit"
        count = content.count(old)
        if count == 0:
            return f"error: 'old' not found in {rel} (must match exactly, including whitespace)"
        if count > 1 and not replace_all:
            return (
                f"error: 'old' appears {count} times in {rel} — add surrounding context to make "
                "it unique, or pass replace_all=true"
            )
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        atomic_write_text(path, updated, newline=newline)  # preserve the file's line endings; atomic
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
        if err := refuse_write(self.workspace, path, self.write_region):
            return err
        rel = kwargs["path"]
        if not path.is_file():
            return f"error: file not found: {rel}"
        try:
            hunks = _parse_hunks(str(kwargs["patch"]))
        except ValueError as exc:
            return f"error: {exc}"
        try:
            content, newline = read_text_for_edit(path)
        except UnicodeDecodeError:
            return f"error: {rel} is not a UTF-8 text file — cannot edit"
        # Anchor EVERY hunk against the ORIGINAL content (not a copy mutated by earlier hunks), so a
        # later hunk can't accidentally match text an earlier hunk inserted. Collect each hunk's span,
        # reject any overlap, then apply all edits by offset in one pass — genuinely all-or-nothing.
        spans: list[tuple[int, int, str, int]] = []  # (start, end, replacement, hunk_number)
        for index, (search, replace) in enumerate(hunks, start=1):
            if search == "":
                return f"error: hunk {index} has an empty SEARCH block (it anchors the edit)"
            occurrences = content.count(search)
            if occurrences == 0:
                return f"error: hunk {index} SEARCH not found (must match exactly, incl. whitespace)"
            if occurrences > 1:
                return f"error: hunk {index} SEARCH is ambiguous ({occurrences} matches) — add context"
            start = content.index(search)
            spans.append((start, start + len(search), replace, index))
        spans.sort()
        for prev, curr in zip(spans, spans[1:], strict=False):
            if curr[0] < prev[1]:
                return f"error: hunks {prev[3]} and {curr[3]} target overlapping regions"
        out: list[str] = []
        cursor = 0
        for start, end, replace, _ in spans:
            out.append(content[cursor:start])
            out.append(replace)
            cursor = end
        out.append(content[cursor:])
        atomic_write_text(path, "".join(out), newline=newline)  # preserve line endings; atomic
        return f"applied {len(hunks)} hunk(s) to {rel}"


class EditBatchTool(_WorkspaceTool):
    """Arm B of ``bench/edit_tools`` — several counted edits, across several files, in one call.

    Two ideas, and only the second is a performance bet.

    **Counted.** Every edit declares how many occurrences it expects (``count``, default 1) and a
    mismatch aborts the whole batch before anything is written. ``edit_file`` already refuses an
    ambiguous single edit — but its ``replace_all`` branch is the one write path with no cardinality
    guard at all: it replaces whatever it finds and *reports* the number afterwards, so a model that
    meant three and hit eleven learns that from the receipt. Declaring the number turns silent
    over-reach into a refusal.

    **Batched across files.** This is the part the bench exists to judge, and it is not adopted on
    anybody's word: the source idea reports 1 edit call against 9–11, measured on models we do not
    run. Our pilot puts arm A between 2 and 11 edit calls per task, with a within-task sd of 0.82.

    ⚠️ **The write region is checked per path, inside the loop.** Every other writer here takes a
    single ``path`` argument, and the guards read that argument — so a multi-file payload with no
    top-level ``path`` would sail past a check that never ran, turning a fenced tool into an
    unfenced one. Every target is resolved and refused individually, in phase 1, before a byte is
    written anywhere.
    """

    name = "edit_batch"
    description = (
        "Apply several exact-match edits across MULTIPLE workspace files in one call. Each edit "
        "declares the number of occurrences it expects ('count', default 1); if any file is "
        "missing, any anchor does not match, or any count differs, NOTHING is written and the "
        "failing edit is named. Prefer this when one change touches several files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "The edits to apply, all-or-nothing.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path relative to the workspace."},
                        "old": {"type": "string", "description": "Exact text to find."},
                        "new": {"type": "string", "description": "Replacement text."},
                        "count": {
                            "type": "integer",
                            "description": "How many occurrences you expect (default 1). A mismatch aborts the batch.",
                        },
                    },
                    "required": ["path", "old", "new"],
                },
            }
        },
        "required": ["edits"],
    }

    def run(self, **kwargs: Any) -> str:
        edits = kwargs.get("edits")
        if not isinstance(edits, list) or not edits:
            return "error: 'edits' must be a non-empty array"

        # --- phase 1: resolve, guard and validate EVERYTHING. No writes. ---
        planned: dict[Path, tuple[str, str]] = {}  # path -> (new content, newline)
        for index, raw in enumerate(edits, start=1):
            if not isinstance(raw, dict):
                return f"error: edit {index} is not an object"
            rel = str(raw.get("path", ""))
            old, new = str(raw.get("old", "")), str(raw.get("new", ""))
            if not rel:
                return f"error: edit {index} has no 'path'"
            if old == "":
                return f"error: edit {index} has an empty 'old' (it anchors the edit)"
            if old == new:
                return f"error: edit {index} has identical 'old' and 'new' — nothing to change"
            try:
                expected = int(raw.get("count", 1))
            except (TypeError, ValueError):
                return f"error: edit {index} has a non-integer 'count'"
            if expected < 1:
                return f"error: edit {index} expects {expected} occurrences — must be >= 1"

            path = resolve_in_workspace(self.workspace, rel)
            # The per-path guard. Outside the loop this would not run for a payload with no
            # top-level `path`, and the tool would write wherever it was told.
            if err := refuse_write(self.workspace, path, self.write_region):
                return f"batch aborted, nothing written — edit {index} ({rel}): {err}"
            if not path.is_file():
                return f"batch aborted, nothing written — edit {index}: file not found: {rel}"

            # Later edits see earlier ones IN THIS BATCH: two edits to one file must compose, and
            # counting both against the original would refuse a legitimate pair.
            if path in planned:
                content, newline = planned[path]
            else:
                try:
                    content, newline = read_text_for_edit(path)
                except UnicodeDecodeError:
                    return f"batch aborted, nothing written — edit {index}: {rel} is not UTF-8 text"

            found = content.count(old)
            if found != expected:
                return (
                    f"batch aborted, NOTHING was written — edit {index} ({rel}): 'old' occurs "
                    f"{found} time(s), expected {expected}. Re-read the file and set 'count' to the "
                    "exact number."
                )
            planned[path] = (content.replace(old, new), newline)

        # --- phase 2: write. ---
        #
        # Atomicity here is about VALIDATION, not about the disk: phase 1 guarantees every edit was
        # resolvable before any write began, and each file lands atomically. It does NOT guarantee
        # that a mid-loop I/O failure leaves nothing behind — so when that happens, say which files
        # already landed instead of reporting a clean abort that is not true.
        written: list[str] = []
        for path, (content, newline) in planned.items():
            try:
                atomic_write_text(path, content, newline=newline)
            except OSError as exc:
                done = ", ".join(written) or "none"
                return (
                    f"error: PARTIAL WRITE — {exc}. Already written: {done}. "
                    f"Failed on: {path.relative_to(self.workspace)}. The workspace is inconsistent."
                )
            written.append(str(path.relative_to(self.workspace)).replace("\\", "/"))
        return f"edited {len(written)} file(s) in one batch: {', '.join(written)}"
