"""Tests for the tool registry and the example tool."""

from __future__ import annotations

import pytest

from chimera.tools import (
    DuplicateToolError,
    EchoTool,
    ToolNotFoundError,
    ToolRegistry,
    default_registry,
)


def test_echo_tool_runs() -> None:
    assert EchoTool().run(text="hi") == "hi"


def test_echo_tool_schema_shape() -> None:
    schema = EchoTool().to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "text" in schema["function"]["parameters"]["properties"]


def test_registry_register_get_run() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    assert "echo" in registry
    assert len(registry) == 1
    assert registry.run("echo", text="ok") == "ok"


def test_registry_rejects_duplicates() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(DuplicateToolError):
        registry.register(EchoTool())
    # replace=True is allowed
    registry.register(EchoTool(), replace=True)


def test_registry_unknown_tool_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.get("nope")


def test_default_registry_has_echo() -> None:
    registry = default_registry()
    assert "echo" in registry
