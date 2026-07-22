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

    untrusted_output: bool = False
    """Whether this tool's result is external content that must be treated as untrusted.

    Declared here, on the interface, rather than being set ad-hoc on individual tools: the taint
    layer keys fencing, sanitisation and run-tainting off it, so a tool that carries it silently and
    a wrapper that silently drops it are the difference between a defended run and an undefended one.
    Wrappers must mirror it — see :func:`chimera.tools.base.is_untrusted_output`, which resolves it
    through a wrapper chain so a wrapper that forgets cannot quietly disarm the defence.
    """


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


def is_untrusted_output(tool: Any) -> bool:
    """True if ``tool`` — or anything it wraps — produces untrusted external content.

    Wrappers (``GovernedTool``, ``LedgeredTool``, …) expose the inner tool as ``.inner``. Reading the
    marker off only the immediate inner meant that composing two wrappers dropped it: with
    ``--guard --taint`` the registry becomes ``LedgeredTool(GovernedTool(tool))``, the middle layer
    did not copy the flag, and document/media/file content stopped being fenced, stopped being
    sanitised, and stopped tainting the run — in the most protective-looking invocation available.
    Walking the chain makes the answer independent of wrapper order and of any future wrapper.
    """
    seen = 0
    current = tool
    while current is not None and seen < 16:  # bounded: a cycle must not hang the agent
        if bool(getattr(current, "untrusted_output", False)):
            return True
        current = getattr(current, "inner", None)
        seen += 1
    return False
