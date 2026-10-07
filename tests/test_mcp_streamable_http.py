"""In-process integration coverage for streamable-HTTP MCP clients."""

from __future__ import annotations

import importlib
import logging
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from chimera.integrations.mcp_client import MCPConnector
from chimera.integrations.mcp_config import McpServerConfig, probe_tools

_TEST_TOKEN = "fake-mcp-token"


class _AuthorizationGate:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.seen: list[bytes] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            token = headers.get(b"authorization", b"")
            self.seen.append(token)
            if token != b"Bearer " + _TEST_TOKEN.encode():
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


@pytest.fixture
def remote_mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, _AuthorizationGate]]:
    # Skipped here and not at module level: the config, cleartext and OAuth tests below need no
    # SDK, and a module-level skip silently dropped them from every run without the `mcp` extra.
    pytest.importorskip("mcp")
    uvicorn = pytest.importorskip("uvicorn")
    fast_mcp = importlib.import_module("mcp.server.fastmcp").FastMCP
    server = fast_mcp("local-test")

    @server.tool()
    def echo(value: str) -> str:
        """Echo test data."""
        return value

    gate = _AuthorizationGate(server.streamable_http_app())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
    config = uvicorn.Config(
        gate, host=host, port=port, log_level="critical", lifespan="on",
        timeout_keep_alive=0, timeout_graceful_shutdown=1,
    )
    app_server = uvicorn.Server(config)
    worker = threading.Thread(target=app_server.run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not app_server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app_server.started, "local MCP test server did not start"
    try:
        yield f"http://{host}:{port}/mcp", gate
    finally:
        app_server.should_exit = True
        app_server.force_exit = True
        worker.join(timeout=5)
        assert not worker.is_alive()


def test_streamable_http_tools_auth_fencing_and_log_safety(
    remote_mcp: tuple[str, _AuthorizationGate], monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    url, gate = remote_mcp
    monkeypatch.setenv("CHIMERA_TEST_MCP_TOKEN", _TEST_TOKEN)
    cfg = McpServerConfig(name="local", url=url, token_env="CHIMERA_TEST_MCP_TOKEN")
    caplog.set_level(logging.DEBUG)

    assert probe_tools(cfg) == [{"name": "echo", "description": "Echo test data."}]

    from chimera.integrations.mcp_client import StreamableHTTPMCPSession

    session = StreamableHTTPMCPSession.from_config(cfg).start()
    try:
        tools = MCPConnector("local", session).tools()
        echo = next(tool for tool in tools if tool.name == "echo")
        result = echo.run(value="untrusted server response")
        assert "untrusted server response" in result
        assert "<<external-data" in result
    finally:
        session.close()

    assert gate.seen
    assert all(value == b"Bearer " + _TEST_TOKEN.encode() for value in gate.seen)
    assert _TEST_TOKEN not in caplog.text



def test_stdio_config_dump_is_byte_compatible() -> None:
    cfg = McpServerConfig(name="stdio", command="python", args=["server.py"], env={"KEY": "value"})
    assert cfg.model_dump() == {
        "name": "stdio", "command": "python", "args": ["server.py"], "env": {"KEY": "value"}
    }


def test_remote_config_serializes_without_adding_null_options() -> None:
    cfg = McpServerConfig(name="remote", url="http://localhost/mcp", token_env="MCP_TOKEN")
    assert cfg.model_dump() == {
        "name": "remote", "command": "", "args": [], "env": {},
        "url": "http://localhost/mcp", "token_env": "MCP_TOKEN",
    }


def test_a_credential_is_never_sent_over_cleartext_to_a_remote_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.integrations.mcp_client import StreamableHTTPMCPSession

    monkeypatch.setenv("CHIMERA_TEST_MCP_TOKEN", _TEST_TOKEN)
    remote = McpServerConfig(
        name="r", url="http://mcp.example.test/mcp", token_env="CHIMERA_TEST_MCP_TOKEN"
    )
    with pytest.raises(ValueError, match="https"):
        StreamableHTTPMCPSession.from_config(remote)
    for ok in ("https://mcp.example.test/mcp", "http://127.0.0.1:9/mcp", "http://localhost:9/mcp"):
        cfg = McpServerConfig(name="r", url=ok, token_env="CHIMERA_TEST_MCP_TOKEN")
        session = StreamableHTTPMCPSession.from_config(cfg)
        assert session.headers["Authorization"].startswith("Bearer ")
    # No credential configured: plain http is the owner's call, since nothing secret travels.
    bare = McpServerConfig(name="r", url="http://mcp.example.test/mcp")
    assert StreamableHTTPMCPSession.from_config(bare).headers == {}


def test_oauth_never_opens_a_browser_outside_an_explicit_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool and autoload run at boot, headless on a server: no stored token means skip."""
    import webbrowser

    from chimera import config_vault
    from chimera.integrations.mcp_config import _session_for

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or False)
    monkeypatch.setattr(config_vault, "read_mcp_token", lambda _name: None)
    cfg = McpServerConfig(
        name="o", url="https://mcp.example.test/mcp",
        oauth_authorization_url="https://auth.example.test/authorize",
        oauth_token_url="https://auth.example.test/token", oauth_client_id="client",
    )
    with pytest.raises(PermissionError, match="mcp test"):
        _session_for(cfg, 1.0)
    assert opened == []
    # A stored token is used without any sign-in, interactive or not.
    monkeypatch.setattr(config_vault, "read_mcp_token", lambda _name: "stored-token")
    assert _session_for(cfg, 1.0).headers == {"Authorization": "Bearer stored-token"}
    assert opened == []


def test_oauth_endpoints_must_be_https() -> None:
    from chimera.integrations.mcp_client import _oauth_authorization_code

    cfg = McpServerConfig(
        name="o", url="https://mcp.example.test/mcp",
        oauth_authorization_url="https://auth.example.test/authorize",
        oauth_token_url="http://auth.example.test/token", oauth_client_id="client",
    )
    with pytest.raises(ValueError, match="token URL"):
        _oauth_authorization_code(cfg, timeout=0.1)
