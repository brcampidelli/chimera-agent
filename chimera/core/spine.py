"""Spine context assembler — ownership-scoped context (anti context-explosion).

Instead of dumping the whole repo, the Spine gathers only the files the task
actually references (by name/path), bounded by size. This is the minimal version of
the Spec Growth Engine's idea: give the agent its ownership path, not free-form
repository search. Richer dependency-aware assembly arrives with the memory layers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*")
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
_MAX_FILE_BYTES = 200_000


def _iter_source_files(workspace: Path) -> Iterator[Path]:
    for path in workspace.rglob("*"):
        if path.is_dir():
            continue
        if any(part in _IGNORE_DIRS for part in path.relative_to(workspace).parts):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def assemble_spine(
    workspace: Path,
    task: str,
    *,
    max_files: int = 12,
    max_chars: int = 12_000,
) -> str:
    """Return a context block of files referenced by ``task`` (empty if none)."""
    root = Path(workspace).resolve()
    mentioned = {token.replace("\\", "/") for token in _TOKEN.findall(task)}
    if not mentioned:
        return ""

    matched: list[Path] = []
    for path in _iter_source_files(root):
        rel = path.relative_to(root).as_posix()
        if path.name in mentioned or rel in mentioned or any(m.endswith(path.name) for m in mentioned):
            matched.append(path)
        if len(matched) >= max_files:
            break
    if not matched:
        return ""

    blocks: list[str] = []
    used = 0
    for path in matched:
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        block = f"### {rel}\n{content[:max_chars]}"
        if used + len(block) > max_chars and blocks:
            break
        blocks.append(block)
        used += len(block)

    if not blocks:
        return ""
    return "Relevant files (ownership-scoped context):\n\n" + "\n\n".join(blocks)
