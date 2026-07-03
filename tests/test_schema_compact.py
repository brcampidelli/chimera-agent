"""Tests for advertise-time tool-schema compaction (no network)."""

from __future__ import annotations

import json
from typing import Any

from chimera.tools.registry import ToolRegistry
from chimera.tools.schema_compact import compact_function_schema, compact_schema, compact_schemas


def _bloated_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_widget",
            "description": "Create a widget. Use this whenever a widget is needed.",
            "parameters": {
                "type": "object",
                "title": "CreateWidgetArgs",
                "properties": {
                    "name": {
                        "type": "string",
                        "title": "Name",
                        "description": "The widget name. Must be unique. Choose carefully.",
                        "examples": ["alpha", "beta"],
                        "default": "widget",
                    },
                    "size": {
                        "type": "string",
                        "enum": ["s", "m", "l"],
                        "description": "Size.",
                    },
                    "body": {
                        "type": "object",
                        "properties": {
                            "color": {"type": "string", "example": "red", "title": "Color"}
                        },
                    },
                },
                "required": ["name"],
            },
        },
    }


def test_compaction_preserves_call_semantics() -> None:
    compact = compact_function_schema(_bloated_tool_schema())
    params = compact["function"]["parameters"]
    assert compact["function"]["name"] == "create_widget"  # selection info preserved
    assert compact["function"]["description"].startswith("Create a widget")
    assert params["type"] == "object"
    assert params["required"] == ["name"]  # required list intact
    assert params["properties"]["size"]["enum"] == ["s", "m", "l"]  # enum intact
    assert params["properties"]["name"]["type"] == "string"  # types intact


def test_compaction_drops_annotation_noise() -> None:
    params = compact_function_schema(_bloated_tool_schema())["function"]["parameters"]
    assert "title" not in params
    name = params["properties"]["name"]
    assert "examples" not in name and "default" not in name and "title" not in name
    # nested body noise is stripped too
    assert "title" not in params["properties"]["body"]["properties"]["color"]
    assert "example" not in params["properties"]["body"]["properties"]["color"]


def test_multi_sentence_description_is_trimmed_to_first_sentence() -> None:
    params = compact_function_schema(_bloated_tool_schema())["function"]["parameters"]
    assert params["properties"]["name"]["description"] == "The widget name."


def test_compaction_is_idempotent() -> None:
    once = compact_function_schema(_bloated_tool_schema())
    twice = compact_function_schema(once)
    assert once == twice


def test_compaction_reduces_size() -> None:
    full = _bloated_tool_schema()
    compact = compact_function_schema(full)
    assert len(json.dumps(compact)) < len(json.dumps(full))


def test_additional_properties_bool_is_preserved() -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": False, "title": "x"}
    out = compact_schema(schema)
    assert out["additionalProperties"] is False
    assert "title" not in out


def test_registry_compact_flag() -> None:
    from chimera.tools.builtin import EchoTool

    registry = ToolRegistry()
    registry.register(EchoTool())
    full = registry.to_openai_schema()
    compact = registry.to_openai_schema(compact=True)
    assert len(full) == len(compact) == 1
    # A round-trip through compact_schemas equals the compact output (same transform).
    assert compact == compact_schemas(full)
