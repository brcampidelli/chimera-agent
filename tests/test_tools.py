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


def test_default_registry_no_longer_has_echo_but_it_can_still_be_registered() -> None:
    # This test used to assert the opposite, and its premise is what changed: `echo` returns its
    # input, so offering it on every surface (terminal, desktop, Discord bot, cron) gave the model a
    # do-nothing tool and resent its schema every step (study 28, P7). It is a test/demo tool now,
    # registered by whoever needs it, and must keep working when they do.
    assert "echo" not in default_registry()
    registry = ToolRegistry()
    registry.register(EchoTool())
    assert registry.get("echo").run(text="hi") == "hi"
