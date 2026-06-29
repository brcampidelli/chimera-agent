"""The ``Tool`` abstraction.

A Tool is a single, well-described capability the agent can invoke (read a file,
run a shell command, fetch a URL, ...). Tools expose an OpenAI/LiteLLM-compatible
function schema so they can be advertised to any model through the gateway.

Skills (see :mod:`chimera.skills`) are higher-level, *learned* procedures that may
compose several tools; tools are the primitive, hand-written building blocks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class Tool(ABC):
    """Base class for a single agent capability.

    Subclasses set :attr:`name`, :attr:`description` and :attr:`parameters`
    (a JSON Schema describing the arguments) and implement :meth:`run`.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

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
