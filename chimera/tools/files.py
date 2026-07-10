"""Filesystem tools: read, write and list within a workspace root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.workspace import resolve_in_workspace
from chimera.tools.write_region import WriteRegion

_MAX_READ_CHARS = 20_000


class _WorkspaceTool(Tool):
    """Base for tools bound to a workspace root (with an optional declared write-region)."""

    def __init__(self, workspace: Path | None = None, *, write_region: WriteRegion | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.write_region = write_region


class ReadFileTool(_WorkspaceTool):
    name = "read_file"
    description = "Read a UTF-8 text file from the workspace."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path relative to the workspace."}},
        "required": ["path"],
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if not path.is_file():
            return f"error: file not found: {kwargs['path']}"
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > _MAX_READ_CHARS:
            return text[:_MAX_READ_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text


class WriteFileTool(_WorkspaceTool):
    name = "write_file"
    description = "Write (create or overwrite) a UTF-8 text file in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["path", "content"],
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if self.write_region is not None and (err := self.write_region.check(path)):
            return err
        content = str(kwargs.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path.relative_to(self.workspace)}"


class ListDirTool(_WorkspaceTool):
    name = "list_dir"
    description = "List entries of a directory in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path relative to the workspace (default '.')."}
        },
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs.get("path", ".")))
        if not path.is_dir():
            return f"error: not a directory: {kwargs.get('path', '.')}"
        entries = sorted(
            f"{p.name}/" if p.is_dir() else p.name for p in path.iterdir()
        )
        return "\n".join(entries) if entries else "(empty)"
