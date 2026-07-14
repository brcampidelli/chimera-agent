"""Tests for the persisted MCP server config store (chimera.integrations.mcp_config).

Pure file I/O — no subprocess, no network. Covers the store contract: add / replace-by-name / remove,
load skipping malformed entries, missing file -> [], and byte-stable serialization.
"""

from __future__ import annotations

from typing import Any

from chimera.integrations.mcp_config import (
    McpServerConfig,
    add_server,
    autoload_into_registry,
    load_servers,
    remove_server,
    save_servers,
)


def _path(tmp_path: Any) -> Any:
    return tmp_path / "mcp.json"


def test_missing_file_loads_empty(tmp_path: Any) -> None:
    assert load_servers(_path(tmp_path)) == []


def test_add_then_load_round_trip(tmp_path: Any) -> None:
    p = _path(tmp_path)
    cfg = McpServerConfig(name="gh", command="npx", args=["-y", "server-github"], env={"TOKEN": "x"})
    add_server(p, cfg)
    loaded = load_servers(p)
    assert len(loaded) == 1
    assert loaded[0].name == "gh"
    assert loaded[0].command == "npx"
    assert loaded[0].args == ["-y", "server-github"]
    assert loaded[0].env == {"TOKEN": "x"}


def test_add_replaces_by_name(tmp_path: Any) -> None:
    p = _path(tmp_path)
    add_server(p, McpServerConfig(name="fs", command="npx", args=["a"]))
    add_server(p, McpServerConfig(name="other", command="uvx"))
    add_server(p, McpServerConfig(name="fs", command="python", args=["b"]))  # replace fs
    loaded = load_servers(p)
    assert len(loaded) == 2
    fs = next(s for s in loaded if s.name == "fs")
    assert fs.command == "python" and fs.args == ["b"]  # the replacement, not the original


def test_remove_returns_bool(tmp_path: Any) -> None:
    p = _path(tmp_path)
    add_server(p, McpServerConfig(name="a", command="x"))
    assert remove_server(p, "missing") is False
    assert remove_server(p, "a") is True
    assert load_servers(p) == []


def test_load_skips_malformed_entries(tmp_path: Any) -> None:
    p = _path(tmp_path)
    # A list where one entry is valid and two are malformed (missing required fields / wrong type).
    p.write_text(
        '[{"name": "ok", "command": "npx"}, {"name": "bad"}, "not-an-object"]',
        encoding="utf-8",
    )
    loaded = load_servers(p)
    assert [s.name for s in loaded] == ["ok"]


def test_unreadable_file_loads_empty(tmp_path: Any) -> None:
    p = _path(tmp_path)
    p.write_text("{ this is not json", encoding="utf-8")
    assert load_servers(p) == []


def test_non_list_json_loads_empty(tmp_path: Any) -> None:
    p = _path(tmp_path)
    p.write_text('{"name": "x"}', encoding="utf-8")  # a dict, not a list
    assert load_servers(p) == []


class _FakeSession:
    """Stands in for StdioMCPSession: start() is a no-op, list_tools() returns fixed specs."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def start(self) -> _FakeSession:
        return self

    def list_tools(self) -> list[Any]:
        from chimera.integrations.mcp_client import MCPToolSpec

        return [MCPToolSpec(name="alpha", description="a"), MCPToolSpec(name="beta", description="b")]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:  # referenced, never called here
        return ""


def _empty_registry() -> Any:
    from chimera.tools.registry import ToolRegistry

    return ToolRegistry()


def test_autoload_no_servers_leaves_registry_unchanged() -> None:
    reg = _empty_registry()
    before = len(list(reg.tools()))
    added = autoload_into_registry(reg, [], connect_timeout=1.0)
    assert added == 0
    assert len(list(reg.tools())) == before


def test_autoload_adds_namespaced_tools(monkeypatch: Any) -> None:
    monkeypatch.setattr("chimera.integrations.StdioMCPSession", _FakeSession)
    reg = _empty_registry()
    servers = [McpServerConfig(name="srv", command="x")]
    added = autoload_into_registry(reg, servers, connect_timeout=1.0)
    assert added == 2
    names = {tool.name for tool in reg.tools()}
    assert "srv_alpha" in names and "srv_beta" in names  # namespaced <server>_<tool>


def test_autoload_skips_a_broken_server(monkeypatch: Any) -> None:
    class _Boom:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def start(self) -> Any:
            raise RuntimeError("cannot launch")

    monkeypatch.setattr("chimera.integrations.StdioMCPSession", _Boom)
    reg = _empty_registry()
    # A broken server is skipped gracefully (no raise), adding 0 tools.
    added = autoload_into_registry(reg, [McpServerConfig(name="bad", command="x")], connect_timeout=1.0)
    assert added == 0


def test_save_is_byte_stable(tmp_path: Any) -> None:
    p = _path(tmp_path)
    servers = [
        McpServerConfig(name="b", command="uvx", args=["x"], env={"K2": "v2", "K1": "v1"}),
        McpServerConfig(name="a", command="npx"),
    ]
    save_servers(p, servers)
    first = p.read_bytes()
    # Re-saving the SAME data yields identical bytes (sorted keys, trailing newline, stable order).
    save_servers(p, servers)
    assert p.read_bytes() == first
    assert first.endswith(b"\n")
    # Round-trips through load without loss.
    loaded = load_servers(p)
    assert [s.name for s in loaded] == ["b", "a"]  # list order preserved (not resorted)
    assert loaded[0].env == {"K2": "v2", "K1": "v1"}
