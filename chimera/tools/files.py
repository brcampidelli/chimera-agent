"""Filesystem tools: read, write and list within a workspace root."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from chimera.tools.base import Tool
from chimera.tools.workspace import resolve_in_workspace

_MAX_READ_CHARS = 20_000


class _WorkspaceTool(Tool):
    """Base for tools bound to a workspace root."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()


class ReadFileTool(_WorkspaceTool):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = "Read a UTF-8 text file from the workspace."
    parameters: ClassVar[dict[str, Any]] = {
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
    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = "Write (create or overwrite) a UTF-8 text file in the workspace."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["path", "content"],
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        content = str(kwargs.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path.relative_to(self.workspace)}"


class ListDirTool(_WorkspaceTool):
    name: ClassVar[str] = "list_dir"
    description: ClassVar[str] = "List entries of a directory in the workspace."
    parameters: ClassVar[dict[str, Any]] = {
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
