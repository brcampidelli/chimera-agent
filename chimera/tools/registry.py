"""A registry that holds the tools available to an agent."""

from __future__ import annotations

from typing import Any

from chimera.telemetry import get_logger
from chimera.tools.base import Tool

_log = get_logger("tools.registry")


class ToolNotFoundError(KeyError):
    """Raised when a tool is requested by a name that is not registered."""


class DuplicateToolError(ValueError):
    """Raised when registering a tool whose name is already taken."""


class ToolRegistry:
    """An ordered collection of uniquely-named tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        if tool.name in self._tools and not replace:
            raise DuplicateToolError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool
        _log.debug("registered tool %s", tool.name)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai_schema(self) -> list[dict[str, Any]]:
        """Schemas for all tools, to advertise to a model."""
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def run(self, name: str, **kwargs: Any) -> str:
        """Look up and execute a tool by name."""
        return self.get(name).run(**kwargs)
