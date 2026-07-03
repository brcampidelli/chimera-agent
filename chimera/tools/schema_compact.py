"""Advertise-time compaction of tool JSON schemas (Improvement #5a).

Tool schemas — especially those imported from MCP servers or OpenAPI specs — carry
verbose annotations (examples, titles, defaults, multi-sentence descriptions, nested
request bodies) that are re-sent to the model on every ReAct step. This strips the
annotation noise and trims parameter prose WITHOUT touching anything that affects tool
selection or argument validity: the function name and description, and every schema's
``type`` / ``properties`` structure / ``required`` / ``enum`` are preserved, so a
compacted schema is semantically identical to advertise but cheaper in tokens.

The canonical schemas on the tools are untouched; compaction produces a copy for the
model only (``ToolRegistry.to_openai_schema(compact=True)``).
"""

from __future__ import annotations

import re
from typing import Any

# Keys that annotate but do not constrain a call — safe to drop from any schema node.
_NOISE_KEYS = frozenset(
    {
        "examples",
        "example",
        "title",
        "default",
        "$comment",
        "externalDocs",
        "readOnly",
        "writeOnly",
        "deprecated",
    }
)
# Sub-schema containers to recurse into so nested descriptions/noise are compacted too.
_SCHEMA_MAP_KEYS = ("properties", "$defs", "definitions", "patternProperties")
_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SCHEMA_VALUE_KEYS = ("items", "additionalProperties", "not", "if", "then", "else")
_MAX_DESC_CHARS = 200
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def _trim_description(text: str) -> str:
    """Keep the first sentence, capped — enough to guide, without the prose tail."""
    text = text.strip()
    if not text:
        return text
    first = _SENTENCE_END.split(text, maxsplit=1)[0]
    if len(first) > _MAX_DESC_CHARS:
        first = first[:_MAX_DESC_CHARS].rstrip() + "…"
    return first


def compact_schema(schema: Any) -> Any:
    """Recursively strip annotation noise and trim descriptions in a JSON schema."""
    if isinstance(schema, list):
        return [compact_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _NOISE_KEYS:
            continue
        if key == "description" and isinstance(value, str):
            trimmed = _trim_description(value)
            if trimmed:
                out[key] = trimmed
        elif key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            out[key] = {name: compact_schema(sub) for name, sub in value.items()}
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            out[key] = [compact_schema(sub) for sub in value]
        elif key in _SCHEMA_VALUE_KEYS:
            out[key] = compact_schema(value)
        else:  # type, enum, required, format, minimum, ... — preserved verbatim
            out[key] = value
    return out


def compact_function_schema(tool_schema: dict[str, Any]) -> dict[str, Any]:
    """Compact one OpenAI function-tool schema's parameters (name/description preserved).

    The function ``description`` drives tool *selection*, so it is left intact; only the
    parameter schema (where the verbose imported prose lives) is compacted.
    """
    fn = tool_schema.get("function")
    if not isinstance(fn, dict) or not isinstance(fn.get("parameters"), dict):
        return tool_schema
    new_fn = {**fn, "parameters": compact_schema(fn["parameters"])}
    return {**tool_schema, "function": new_fn}


def compact_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact a list of OpenAI function-tool schemas."""
    return [compact_function_schema(schema) for schema in schemas]
