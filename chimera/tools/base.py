"""The ``Tool`` abstraction.

A Tool is a single, well-described capability the agent can invoke (read a file,
run a shell command, fetch a URL, ...). Tools expose an OpenAI/LiteLLM-compatible
function schema so they can be advertised to any model through the gateway.

``name``, ``description`` and ``parameters`` are instance attributes: static tools
set them as class attributes, while *dynamically generated* tools (from an OpenAPI
spec or an MCP server) set them per instance in ``__init__``.

Skills (see :mod:`chimera.skills`) are higher-level, *learned* procedures that may
compose several tools; tools are the primitive, hand-written building blocks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Base class for a single agent capability."""

    name: str
    description: str
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    @abstractmethod
    def run(self, **kwargs: Any) -> str:
        """Execute the tool and return a string result."""
        raise NotImplementedError

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the function-tool schema understood by LiteLLM/OpenAI APIs."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
