"""A minimal set of native tools shipped with Chimera.

This will grow into the full built-in toolset (files, shell, http, git, ...). For
now it carries one trivial, dependency-free tool that exercises the registry and
serves as the canonical example of the :class:`~chimera.tools.base.Tool` pattern.
"""

from __future__ import annotations

from typing import Any, ClassVar

from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


class EchoTool(Tool):
    """Return the provided text unchanged (useful for tests and demos)."""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "Echo back the given text exactly."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo."}},
        "required": ["text"],
    }

    def run(self, **kwargs: Any) -> str:
        return str(kwargs.get("text", ""))


def default_registry() -> ToolRegistry:
    """Build a registry pre-populated with the built-in native tools."""
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry
