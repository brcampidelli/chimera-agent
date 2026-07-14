"""Tools inventory for the desktop app: a pure read-model over the agent's tool registry.

The desktop factory builds its registry with :func:`chimera.tools.builtin.default_registry`, so the
``GET /api/tools`` handler builds the SAME thing and hands the registry here. This helper only reads
each tool's already-set instance attributes (``.name``, ``.description``, ``.parameters``) — it never
runs a tool, and iterating the registry is side-effect free.

Honesty rules baked in here:

- The capability ``tags`` are derived PURELY from the tool NAME against the governance capability sets
  (:mod:`chimera.governance.ledger`: ``FETCH_TOOLS`` → ``network``, ``READ_TOOLS`` → ``read``,
  ``WRITE_TOOLS`` → ``write``, ``EXEC_TOOLS`` → ``exec``, ``SIDE_EFFECT_TOOLS`` → ``side-effect``). They
  are a static classification of what the tool CAN do by name — not an observation of anything executed.
  A tool whose name is in none of the sets carries no tags, and that empty list is shown as-is.
- ``untrusted_output`` is read straight off the tool (``getattr(tool, "untrusted_output", False)``).
  MCP/OpenAPI-imported tools set it True; the native/key-gated desktop registry has none, so it is
  honestly False across the board there — read anyway rather than assumed.
- ``params`` are the top-level keys of ``parameters["properties"]`` (``[]`` when a tool takes none);
  they are the parameter NAMES, not a re-derived schema.
"""

from __future__ import annotations

from typing import Any

from chimera.governance.ledger import (
    EXEC_TOOLS,
    FETCH_TOOLS,
    READ_TOOLS,
    SIDE_EFFECT_TOOLS,
    WRITE_TOOLS,
)

# Governance capability set → tag label, in a STABLE render order (network/read/write/exec/side-effect).
# A tool NAME is matched against each set; a name can in principle appear in more than one, so all
# matching tags are emitted in this order.
_TAG_SETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("network", FETCH_TOOLS),
    ("read", READ_TOOLS),
    ("write", WRITE_TOOLS),
    ("exec", EXEC_TOOLS),
    ("side-effect", SIDE_EFFECT_TOOLS),
)


def _tags_for(name: str) -> list[str]:
    """The capability tags for a tool NAME, derived from the governance sets in stable order."""
    return [tag for tag, names in _TAG_SETS if name in names]


def list_tools(registry: Any) -> list[dict[str, Any]]:
    """Return one honest info dict per registered tool.

    ``registry`` is a :class:`~chimera.tools.registry.ToolRegistry`; ``registry.tools()`` yields the
    Tool instances. Each dict is ``{name, description, params, tags, untrusted_output}`` — all read
    defensively from the tool's instance attributes, nothing executed. See the module docstring for the
    honesty guarantees on ``tags`` and ``untrusted_output``.
    """
    infos: list[dict[str, Any]] = []
    for tool in registry.tools():
        name = str(getattr(tool, "name", "") or "")
        parameters = getattr(tool, "parameters", None) or {}
        properties = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        params = list(properties) if isinstance(properties, dict) else []
        infos.append(
            {
                "name": name,
                "description": str(getattr(tool, "description", "") or ""),
                "params": params,
                "tags": _tags_for(name),
                "untrusted_output": bool(getattr(tool, "untrusted_output", False)),
            }
        )
    return infos
