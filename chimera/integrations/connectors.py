"""Connector abstraction and registry.

A *connector* turns an external system (an MCP server, an OpenAPI service, ...)
into a set of :class:`~chimera.tools.base.Tool` objects the agent can use. The
:class:`ConnectorRegistry` aggregates connectors and can pour their tools into a
:class:`~chimera.tools.registry.ToolRegistry`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

_log = get_logger("integrations.connectors")


class Connector(ABC):
    """A source of tools from an external system."""

    name: str

    @abstractmethod
    def tools(self) -> list[Tool]:
        """Return the tools this connector exposes."""
        raise NotImplementedError


class ConnectorRegistry:
    """Holds named connectors and aggregates their tools."""

    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.name] = connector
        _log.debug("registered connector %s", connector.name)

    def get(self, name: str) -> Connector:
        return self._connectors[name]

    def names(self) -> list[str]:
        return list(self._connectors)

    def all_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for connector in self._connectors.values():
            tools.extend(connector.tools())
        return tools

    def into_tool_registry(self, registry: ToolRegistry) -> int:
        """Register every connector tool into ``registry``. Returns the count."""
        count = 0
        for tool in self.all_tools():
            registry.register(tool, replace=True)
            count += 1
        return count
