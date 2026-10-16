"""Tests for the persisted MCP server config store (chimera.integrations.mcp_config).

Pure file I/O — no subprocess, no network. Covers the store contract: add / replace-by-name / remove,
load skipping malformed entries, missing file -> [], and byte-stable serialization.
"""

from __future__ import annotations

from typing import Any

from chimera.integrations.mcp_config import (
    McpServerConfig,
    _session_for,
    add_server,
    autoload_into_registry,
    load_servers,
    remove_server,
    save_servers,
    set_server_disabled,
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
    assert loaded[0].disabled is False


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


def test_disabled_server_round_trips_and_can_be_enabled(tmp_path: Any) -> None:
    path = _path(tmp_path)
    add_server(path, McpServerConfig(name="kept", command="server", disabled=True))
    assert load_servers(path)[0].disabled is True
    assert set_server_disabled(path, "kept", False) is True
    assert load_servers(path)[0].disabled is False
    assert set_server_disabled(path, "missing", True) is False


def test_disabled_server_is_not_connected_or_added_to_the_registry(monkeypatch: Any) -> None:
    class _MustNotConnect:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("disabled MCP server was constructed")

    monkeypatch.setattr("chimera.integrations.StdioMCPSession", _MustNotConnect)
    reg = _empty_registry()
    config = McpServerConfig(name="paused", command="server", disabled=True)
    assert autoload_into_registry(reg, [config]) == 0
    assert list(reg.tools()) == []


def test_runtime_environment_references_resolve_without_mutating_config(
    monkeypatch: Any, tmp_path: Any
) -> None:
    monkeypatch.setenv("CHIMERA_MCP_RUNTIME_SECRET", "secret-runtime-value")
    cfg = McpServerConfig(
        name="runtime", command="server", args=["--token={env:CHIMERA_MCP_RUNTIME_SECRET}"],
        env={"TOKEN": "{env:CHIMERA_MCP_RUNTIME_SECRET}"},
    )
    path = _path(tmp_path)
    save_servers(path, [cfg])
    session = _session_for(cfg, 3.0)
    assert session.args == ["--token=secret-runtime-value"]
    assert session.env == {"TOKEN": "secret-runtime-value"}
    assert "secret-runtime-value" not in path.read_text(encoding="utf-8")
    assert cfg.env["TOKEN"] == "{env:CHIMERA_MCP_RUNTIME_SECRET}"


def test_missing_runtime_environment_reference_fails_clearly(monkeypatch: Any) -> None:
    monkeypatch.delenv("CHIMERA_MCP_MISSING_SECRET", raising=False)
    cfg = McpServerConfig(name="runtime", command="server", env={"TOKEN": "{env:CHIMERA_MCP_MISSING_SECRET}"})
    try:
        _session_for(cfg, 3.0)
    except ValueError as exc:
        assert "CHIMERA_MCP_MISSING_SECRET" in str(exc)
    else:
        raise AssertionError("missing environment reference was accepted")


def test_cwd_is_resolved_against_current_directory_and_missing_path_rejected(
    monkeypatch: Any, tmp_path: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace").mkdir()
    cfg = McpServerConfig(name="cwd", command="server", cwd="workspace")
    assert _session_for(cfg, 3.0).cwd == str((tmp_path / "workspace").resolve())
    bad = McpServerConfig(name="cwd", command="server", cwd="does-not-exist")
    try:
        _session_for(bad, 3.0)
    except ValueError as exc:
        assert "cwd does not exist" in str(exc)
    else:
        raise AssertionError("nonexistent cwd was accepted")


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
