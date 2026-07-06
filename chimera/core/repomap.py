"""Repo-map — a compact structural digest of the workspace for the agent's context.

The agent could grep and read files, but on a large repo it wastes turns just finding where a
symbol lives. A repo-map front-loads that: one line per Python file with its top-level functions
and classes, extracted with the stdlib ``ast`` (no dependency, no model call). Dropped into the
solve context, it lets the agent jump straight to the right file instead of exploring blind.

Deliberately cheap and bounded: it skips build/venv/cache noise and honors a light ``.gitignore``,
lists only top-level symbols (not every method), and truncates to a character budget — a map is a
table of contents, not the code.
"""

from __future__ import annotations

import ast
import fnmatch
import os
from pathlib import Path

_DEFAULT_IGNORE = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".chimera", ".idea", ".vscode",
}


def _load_gitignore(root: Path) -> list[str]:
    """Read simple glob patterns from a top-level .gitignore (comments/blank lines skipped)."""
    path = root / ".gitignore"
    if not path.is_file():
        return []
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped.rstrip("/"))
    return patterns


def _is_ignored(name: str, rel_posix: str, patterns: list[str]) -> bool:
    if name in _DEFAULT_IGNORE:
        return True
    return any(
        fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, f"*/{pat}")
        for pat in patterns
    )


def _symbols(path: Path) -> list[str]:
    """Top-level function and class names in a Python file (empty on a parse error)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.append(f"{node.name}")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(f"{node.name}()")
    return names


def build_repo_map(root: Path, *, max_chars: int = 4000) -> str:
    """A one-line-per-file map of the workspace's Python symbols, truncated to ``max_chars``.

    Empty string when there are no Python files. Directories in the ignore set (and simple
    .gitignore globs) are pruned. Files past the budget are dropped with a trailing note.
    """
    root = root.resolve()
    patterns = _load_gitignore(root)
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        # Prune ignored directories in place so os.walk never descends into them.
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not _is_ignored(d, (rel_dir / d).as_posix(), patterns)
        ]
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            rel = (rel_dir / filename).as_posix().lstrip("./")
            if _is_ignored(filename, rel, patterns):
                continue
            symbols = _symbols(Path(dirpath) / filename)
            lines.append(f"{rel}: {', '.join(symbols)}" if symbols else rel)

    lines.sort()
    out: list[str] = []
    used = 0
    omitted = 0
    for line in lines:
        if used + len(line) + 1 > max_chars:
            omitted += 1
            continue
        out.append(line)
        used += len(line) + 1
    text = "\n".join(out)
    if omitted:
        text += f"\n... [{omitted} more file(s) omitted for space]"
    return text
