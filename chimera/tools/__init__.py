"""Native tools — primitive, hand-written capabilities the agent can invoke."""

from chimera.tools.base import Tool
from chimera.tools.builtin import EchoTool, default_registry
from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from chimera.tools.http import HttpGetTool
from chimera.tools.registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)
from chimera.tools.shell import RunShellTool
from chimera.tools.workspace import PathEscapesWorkspaceError, resolve_in_workspace

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "DuplicateToolError",
    "EchoTool",
    "ReadFileTool",
    "WriteFileTool",
    "ListDirTool",
    "RunShellTool",
    "HttpGetTool",
    "PathEscapesWorkspaceError",
    "resolve_in_workspace",
    "default_registry",
]
