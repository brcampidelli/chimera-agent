"""Content and path search tools — grep (regex over file contents) and glob (path patterns).

The read-only discovery primitives a repository explorer needs (alongside read_file /
list_dir). Both are workspace-rooted, skip noise directories, and bound their output so a
search never floods the agent's context. Binary/undecodable files are skipped, not errored.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from chimera.tools.files import _WorkspaceTool
from chimera.tools.workspace import resolve_in_workspace

_IGNORE_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
     ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".tox", "site-packages"}
)
_MAX_RESULTS = 100
_MAX_FILE_BYTES = 1_000_000


def _walk_files(root: Path) -> list[Path]:
    """All non-ignored files under ``root`` (deterministic order)."""
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in _IGNORE_DIRS for part in path.parts):
            continue
        if path.is_file():
            out.append(path)
    return out


class GrepTool(_WorkspaceTool):
    name = "grep"
    description = (
        "Search file contents by regular expression. Returns 'relpath:lineno: line' matches. "
        "Optionally restrict to a subdirectory and to files matching a glob (e.g. '*.py')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression to search for."},
            "glob": {"type": "string", "description": "Optional filename glob filter, e.g. '*.py'."},
            "path": {"type": "string", "description": "Optional subdirectory to search (default '.')."},
            "max_results": {"type": "integer", "description": f"Max matches (default {_MAX_RESULTS})."},
        },
        "required": ["pattern"],
    }

    def run(self, **kwargs: Any) -> str:
        try:
            regex = re.compile(str(kwargs["pattern"]))
        except re.error as exc:
            return f"error: invalid regex: {exc}"
        root = resolve_in_workspace(self.workspace, str(kwargs.get("path", ".")))
        if not root.is_dir():
            return f"error: not a directory: {kwargs.get('path', '.')}"
        glob = kwargs.get("glob")
        limit = int(kwargs.get("max_results") or _MAX_RESULTS)

        hits: list[str] = []
        for file in _walk_files(root):
            if glob and not file.match(str(glob)):
                continue
            try:
                if file.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable — skip, don't error
            rel = file.relative_to(self.workspace).as_posix()
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        return "\n".join(hits) + f"\n... [stopped at {limit} matches]"
        return "\n".join(hits) if hits else "no matches"


class GlobTool(_WorkspaceTool):
    name = "glob"
    description = "Find files by path pattern (e.g. '**/*.py', 'src/**/test_*.py'). Returns relative paths."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'."},
            "max_results": {"type": "integer", "description": f"Max paths (default {_MAX_RESULTS})."},
        },
        "required": ["pattern"],
    }

    def run(self, **kwargs: Any) -> str:
        pattern = str(kwargs["pattern"])
        limit = int(kwargs.get("max_results") or _MAX_RESULTS)
        root = self.workspace.resolve()
        out: list[str] = []
        for path in sorted(self.workspace.glob(pattern)):
            if any(part in _IGNORE_DIRS for part in path.parts):
                continue
            # A pattern like '../../etc/passwd' or one crossing a symlink can escape the workspace.
            # Resolve and require the real path to stay under the workspace root before emitting it.
            resolved = path.resolve()
            if resolved != root and root not in resolved.parents:
                continue
            if resolved.is_file():
                out.append(resolved.relative_to(root).as_posix())
                if len(out) >= limit:
                    break
        return "\n".join(out) if out else "no files match"
