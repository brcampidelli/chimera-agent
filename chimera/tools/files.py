"""Filesystem tools: read, write and list within a workspace root."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.workspace import atomic_write_text, resolve_in_workspace
from chimera.tools.write_region import WriteRegion, refuse_write

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

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        write_region: WriteRegion | None = None,
        trust_workspace: bool = True,
    ) -> None:
        super().__init__(workspace, write_region=write_region)
        # When the workspace is NOT trusted (running on third-party code), a file's contents are
        # untrusted external input — a poisoned source/README can carry a prompt injection. Marking
        # the output untrusted routes it through the taint ledger + fence (ledger_tool.py reads this
        # attribute), so the run taints and the dangerous-tool gate arms, just like a fetched page.
        # Default trusts the workspace (your own repo) so `--taint` isn't tripped by every file read.
        self.untrusted_output = not trust_workspace

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if not path.is_file():
            return f"error: file not found: {kwargs['path']}"
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > _MAX_READ_CHARS:
            return text[:_MAX_READ_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text


def syntax_error(path: Path, content: str) -> str | None:
    """Why ``content`` cannot be the whole of ``path``, or None if it can.

    Only formats we can check for free with the standard library, and only on a FULL overwrite.
    That scope is the whole idea: a surgical edit may legitimately pass through a state that does
    not parse — a half-applied rename, a function being moved — while a complete file that does not
    parse is never what anyone meant. Checking both would turn a normal editing rhythm into a fight.
    """
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            where = f" (line {exc.lineno}" + (f", col {exc.offset}" if exc.offset else "") + ")"
            return f"{exc.msg}{where}"
        except ValueError as exc:  # e.g. source with NUL bytes
            return str(exc)
    elif suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return f"{exc.msg} (line {exc.lineno}, col {exc.colno})"
    return None


class WriteFileTool(_WorkspaceTool):
    name = "write_file"
    description = "Write (create or overwrite) a UTF-8 text file in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "content": {"type": "string", "description": "Full file content to write."},
            "allow_invalid": {
                "type": "boolean",
                "description": (
                    "Write even if the content does not parse (.py/.json). Use for templates, "
                    "fixtures and partial files that are invalid on purpose."
                ),
            },
        },
        "required": ["path", "content"],
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if err := refuse_write(self.workspace, path, self.write_region):
            return err
        content = str(kwargs.get("content", ""))
        # Refused BEFORE the write, not reported after. A file that does not parse is one the next
        # step cannot read, import or test, so writing it costs a whole verify cycle to discover
        # something `ast.parse` knew for free — and on a full overwrite it also destroys the version
        # that did parse.
        #
        # The escape hatch is not optional. Templates, fixtures and deliberately-broken examples are
        # real files, and a guard with no way past turns "the agent wrote something odd" into "the
        # agent cannot do this at all".
        if not bool(kwargs.get("allow_invalid", False)) and (why := syntax_error(path, content)):
            return (
                f"error: refused to overwrite {path.name} — the content is not valid "
                f"{path.suffix.lstrip('.')}: {why}. Fix it, or pass allow_invalid=true if the file "
                f"is meant to be invalid."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Byte-exact atomic write: never OS-translate the model's newlines, and never truncate an
        # existing file if the write is interrupted (temp + replace).
        atomic_write_text(path, content)
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
