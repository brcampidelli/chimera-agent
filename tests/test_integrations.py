"""Tests for connectors, the OpenAPI importer, and the MCP wrapping layer."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.integrations import (
    ConnectorRegistry,
    MCPConnector,
    MCPToolSpec,
    OpenAPIConnector,
    load_spec,
    tools_from_openapi,
)
from chimera.tools import ToolRegistry

SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/items/{id}": {
            "get": {
                "operationId": "get_item",
                "summary": "Get an item",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
                ],
            }
        },
        "/items": {
            "post": {
                "operationId": "create_item",
                "summary": "Create item",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                        }
                    },
                },
            }
        },
    },
}


def test_openapi_generates_tools() -> None:
    tools = {t.name: t for t in tools_from_openapi(SPEC)}
    assert set(tools) == {"get_item", "create_item"}

    get_item = tools["get_item"]
    assert get_item.method == "GET"
    assert get_item.path_params == ["id"]
    assert get_item.query_params == ["verbose"]
    assert get_item.parameters["required"] == ["id"]

    create_item = tools["create_item"]
    assert create_item.has_body is True
    assert "body" in create_item.parameters["properties"]
    assert create_item.parameters["required"] == ["body"]


def test_rest_tool_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(httpx, "request", fake_request)

    tool = {t.name: t for t in tools_from_openapi(SPEC)}["get_item"]
    out = tool.run(id="42", verbose="true")

    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.example.com/items/42"
    assert captured["params"] == {"verbose": "true"}
    assert "[200]" in out


def test_rest_tool_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    import httpx

    calls = {"n": 0}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(status_code=429, text="rate limited", headers={})
        return SimpleNamespace(status_code=200, text="ok", headers={})

    monkeypatch.setattr(httpx, "request", fake_request)
    monkeypatch.setattr(time, "sleep", lambda _s: None)  # no real backoff delay

    tool = {t.name: t for t in tools_from_openapi(SPEC)}["get_item"]
    out = tool.run(id="42")
    assert calls["n"] == 2  # retried once after the 429
    assert "[200]" in out


def test_rest_tool_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    import httpx

    calls = {"n": 0}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "request", fake_request)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    tool = {t.name: t for t in tools_from_openapi(SPEC, retries=2)}["get_item"]
    out = tool.run(id="42")
    assert calls["n"] == 3  # retries + 1 attempts
    assert out.startswith("error:")


def test_openapi_connector_into_registry() -> None:
    connector = OpenAPIConnector("example", SPEC)
    creg = ConnectorRegistry()
    creg.register(connector)

    treg = ToolRegistry()
    count = creg.into_tool_registry(treg)
    assert count == 2
    assert "get_item" in treg
    assert "create_item" in treg


def test_load_spec_json(tmp_path: Path) -> None:
    spec_path = tmp_path / "api.json"
    spec_path.write_text(json.dumps(SPEC), encoding="utf-8")
    loaded = load_spec(spec_path)
    assert loaded["openapi"] == "3.0.0"


class FakeMCPSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[MCPToolSpec]:
        return [
            MCPToolSpec(
                name="search",
                description="Search the knowledge base",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return f"result for {name}"


def test_mcp_connector_wraps_tools() -> None:
    session = FakeMCPSession()
    connector = MCPConnector("kb", session)
    tools = connector.tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "search"
    assert tool.parameters["properties"]["q"]["type"] == "string"

    out = tool.run(q="hello")
    assert out == "result for search"
    assert session.calls == [("search", {"q": "hello"})]


def test_mcp_connector_name_prefix() -> None:
    connector = MCPConnector("kb", FakeMCPSession(), name_prefix="kb.")
    assert connector.tools()[0].name == "kb.search"
