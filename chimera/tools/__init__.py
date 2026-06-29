"""Native tools — primitive, hand-written capabilities the agent can invoke."""

from chimera.tools.base import Tool
from chimera.tools.builtin import EchoTool, default_registry
from chimera.tools.registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolNotFoundError",
    "DuplicateToolError",
    "EchoTool",
    "default_registry",
]
