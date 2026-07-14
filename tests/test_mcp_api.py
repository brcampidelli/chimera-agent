"""Tests for the MCP / Integrations desktop API (config CRUD + monkeypatched live test).

No subprocess is ever spawned: the ONLY connecting call (``POST /api/mcp/{name}/test``) is exercised
by monkeypatching ``chimera.api.mcp_api._live_test``. Skipped when the 'desktop' extra is absent.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402
from chimera.interface.session import SupportsRun  # noqa: E402


def _client(tmp_path: Any) -> TestClient:
    from typing import cast

    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(cast(SupportsRun, None)), settings=settings))


def test_mcp_crud_and_env_values_not_returned(tmp_path: Any) -> None:
    client = _client(tmp_path)

    # Empty to start.
    assert client.get("/api/mcp").json() == {"servers": [], "count": 0}

    # Add one with an env secret.
    body = {
        "name": "gh",
        "command": "npx",
        "args": ["-y", "server-github"],
        "env": {"GITHUB_TOKEN": "ghp_supersecret"},
    }
    resp = client.post("/api/mcp", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    server = data["servers"][0]
    assert server["name"] == "gh"
    assert server["command"] == "npx"
    assert server["args"] == ["-y", "server-github"]
    # The env VALUE is never returned — only the key NAME.
    assert server["env_keys"] == ["GITHUB_TOKEN"]
    assert "ghp_supersecret" not in resp.text

    # Replace-by-name via a second POST.
    client.post("/api/mcp", json={"name": "gh", "command": "docker", "args": [], "env": {}})
    listed = client.get("/api/mcp").json()
    assert listed["count"] == 1
    assert listed["servers"][0]["command"] == "docker"

    # Delete.
    assert client.delete("/api/mcp/gh").json() == {"deleted": True}
    assert client.delete("/api/mcp/gh").json() == {"deleted": False}
    assert client.get("/api/mcp").json()["count"] == 0


def test_mcp_test_ok_with_monkeypatched_connector(tmp_path: Any, monkeypatch: Any) -> None:
    client = _client(tmp_path)
    client.post("/api/mcp", json={"name": "fake", "command": "x", "args": [], "env": {}})

    def fake_live_test(cfg: Any) -> list[dict[str, str]]:
        assert cfg.name == "fake"  # the resolved config is passed through
        return [
            {"name": "search", "description": "search things"},
            {"name": "fetch", "description": "fetch a url"},
        ]

    monkeypatch.setattr("chimera.api.mcp_api._live_test", fake_live_test)
    resp = client.post("/api/mcp/fake/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert [t["name"] for t in data["tools"]] == ["search", "fetch"]


def test_mcp_test_failure_is_short_and_secret_free(tmp_path: Any, monkeypatch: Any) -> None:
    client = _client(tmp_path)
    client.post("/api/mcp", json={"name": "boom", "command": "x", "args": [], "env": {}})

    def boom(cfg: Any) -> list[dict[str, str]]:
        raise TimeoutError("connect timed out")

    monkeypatch.setattr("chimera.api.mcp_api._live_test", boom)
    resp = client.post("/api/mcp/boom/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["tools"] == []
    assert data["error"] == "connect timed out"


def test_mcp_test_unknown_server(tmp_path: Any) -> None:
    client = _client(tmp_path)
    data = client.post("/api/mcp/ghost/test").json()
    assert data == {"ok": False, "tools": [], "error": "no such server"}
