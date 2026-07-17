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

    def to_openai_schema(self, *, compact: bool = False) -> list[dict[str, Any]]:
        """Schemas for all tools, to advertise to a model.

        With ``compact=True``, annotation noise is stripped and parameter prose trimmed
        at advertise-time (semantics preserved) to cut the tokens re-sent every step.
        """
        schemas = [tool.to_openai_schema() for tool in self._tools.values()]
        if compact:
            from chimera.tools.schema_compact import compact_schemas

            return compact_schemas(schemas)
        return schemas

    def run(self, name: str, **kwargs: Any) -> str:
        """Look up and execute a tool by name."""
        from chimera.obs import span

        with span("tool.run", **{"tool.name": name}) as sp:
            out = self.get(name).run(**kwargs)
            sp.set(**{"tool.ok": not out.startswith("error:"), "tool.output_chars": len(out)})
            return out
