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

pytest.importorskip("mcp")
uvicorn = pytest.importorskip("uvicorn")
FastMCP = importlib.import_module("mcp.server.fastmcp").FastMCP

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
    server = FastMCP("local-test")

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
