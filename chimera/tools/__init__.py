"""Native tools — primitive, hand-written capabilities the agent can invoke.

**Why the re-exports are lazy.** See :mod:`chimera.eval` for the rationale. There is a second reason
here: eagerly importing every tool from this ``__init__`` created a genuine import cycle that only the
*order* of a sibling package's eager imports was hiding. ``chimera.governance.ledger_tool`` needs
``chimera.tools.base.Tool``; importing it ran this ``__init__``, which pulled ``browser`` / ``documents``
/ ``scrape``, each of which imports ``fence`` back from ``ledger_tool`` — half-initialised. Resolving
names on first access (PEP 562) means ``from chimera.tools.base import Tool`` costs exactly that module,
so the cycle cannot form. ``from chimera.tools import BrowserTool`` still works.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.tools.base import Tool
    from chimera.tools.browser import BrowserTool, Element, render_elements
    from chimera.tools.builtin import EchoTool, default_registry
    from chimera.tools.documents import ReadDocumentTool
    from chimera.tools.edit import ApplyPatchTool, EditFileTool
    from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
    from chimera.tools.http import HttpGetTool
    from chimera.tools.registry import (
        DuplicateToolError,
        ToolNotFoundError,
        ToolRegistry,
    )
    from chimera.tools.search import GlobTool, GrepTool
    from chimera.tools.shell import RunShellTool
    from chimera.tools.workspace import PathEscapesWorkspaceError, resolve_in_workspace

_LAZY: dict[str, tuple[str, str]] = {
    "Tool": ("base", "Tool"),
    "BrowserTool": ("browser", "BrowserTool"),
    "Element": ("browser", "Element"),
    "render_elements": ("browser", "render_elements"),
    "EchoTool": ("builtin", "EchoTool"),
    "default_registry": ("builtin", "default_registry"),
    "ReadDocumentTool": ("documents", "ReadDocumentTool"),
    "ApplyPatchTool": ("edit", "ApplyPatchTool"),
    "EditFileTool": ("edit", "EditFileTool"),
    "ListDirTool": ("files", "ListDirTool"),
    "ReadFileTool": ("files", "ReadFileTool"),
    "WriteFileTool": ("files", "WriteFileTool"),
    "HttpGetTool": ("http", "HttpGetTool"),
    "DuplicateToolError": ("registry", "DuplicateToolError"),
    "ToolNotFoundError": ("registry", "ToolNotFoundError"),
    "ToolRegistry": ("registry", "ToolRegistry"),
    "GlobTool": ("search", "GlobTool"),
    "GrepTool": ("search", "GrepTool"),
    "RunShellTool": ("shell", "RunShellTool"),
    "PathEscapesWorkspaceError": ("workspace", "PathEscapesWorkspaceError"),
    "resolve_in_workspace": ("workspace", "resolve_in_workspace"),
}


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name on first use, then cache it (PEP 562)."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    submodule, attribute = target
    value = getattr(import_module(f"{__name__}.{submodule}"), attribute)
    globals()[name] = value  # subsequent lookups skip __getattr__ entirely
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "DuplicateToolError",
    "EchoTool",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ApplyPatchTool",
    "ReadDocumentTool",
    "BrowserTool",
    "Element",
    "render_elements",
    "ListDirTool",
    "GrepTool",
    "GlobTool",
    "RunShellTool",
    "HttpGetTool",
    "PathEscapesWorkspaceError",
    "resolve_in_workspace",
    "default_registry",
]
