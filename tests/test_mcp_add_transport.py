"""POST /api/mcp takes a stdio command or a local URL, and refuses a header sent over cleartext.

A header carries a credential more often than not, so the route applies the same transport rule the
client applies on connect: https, or http to this machine only. Refusals are the caller's request,
so they come back as 400, not as a server fault.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import mcp_api
from chimera.config import Settings
from chimera.integrations.mcp_config import McpServerConfig
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun


def _client(tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(cast(SupportsRun, None)), settings=settings))


def test_a_local_url_with_headers_is_saved_and_listed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "name": "voicestudio",
        "url": "http://127.0.0.1:3900/mcp/",
        "headers": {"X-OmniVoice-Client-Id": "chimera"},
    }

    resp = client.post("/api/mcp", json=body)

    assert resp.status_code == 200, resp.text
    assert [server["name"] for server in resp.json()["servers"]] == ["voicestudio"]
    # The listing never echoes a URL or a header value; the file is where the endpoint is kept.
    stored = json.loads((tmp_path / "home" / "mcp.json").read_text(encoding="utf-8"))
    assert stored[0]["url"] == "http://127.0.0.1:3900/mcp/"
    assert stored[0]["headers"] == {"X-OmniVoice-Client-Id": "chimera"}


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "url": "http://example.com/mcp", "headers": {"Authorization": "Bearer t"}},
        {"name": "x", "url": "http://192.168.1.2/mcp", "headers": {"Authorization": "Bearer t"}},
    ],
)
def test_headers_over_cleartext_to_another_host_are_refused_as_400(tmp_path: Path, body: dict[str, Any]) -> None:
    client = _client(tmp_path)

    resp = client.post("/api/mcp", json=body)

    assert resp.status_code == 400
    assert "https" in resp.json()["detail"]
    assert client.get("/api/mcp").json()["count"] == 0, "a refused request saves nothing"


def test_headers_without_a_url_are_refused_as_400(tmp_path: Path) -> None:
    client = _client(tmp_path)

    resp = client.post("/api/mcp", json={"name": "x", "command": "npx", "headers": {"A": "b"}})

    assert resp.status_code == 400


def test_a_url_and_a_command_together_are_refused_as_400(tmp_path: Path) -> None:
    client = _client(tmp_path)

    resp = client.post(
        "/api/mcp", json={"name": "x", "command": "npx", "url": "http://127.0.0.1:3900/mcp/"}
    )

    assert resp.status_code == 400


def test_https_headers_are_accepted(tmp_path: Path) -> None:
    client = _client(tmp_path)

    resp = client.post(
        "/api/mcp",
        json={"name": "remote", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer t"}},
    )

    assert resp.status_code == 200, resp.text


def test_fingerprint_keeps_existing_servers_and_changes_with_header_names() -> None:
    plain = McpServerConfig(name="s", command="npx", args=["-y", "x"])
    before = mcp_api._fingerprint(plain)
    # A server with no headers hashes exactly as it did before the field existed.
    assert mcp_api._fingerprint(McpServerConfig(name="s", command="npx", args=["-y", "x"])) == before

    with_header = McpServerConfig(name="s", url="http://127.0.0.1:3900/mcp/", headers={"X-A": "1"})
    renamed = McpServerConfig(name="s", url="http://127.0.0.1:3900/mcp/", headers={"X-B": "1"})
    assert mcp_api._fingerprint(with_header) != mcp_api._fingerprint(renamed)


def test_fingerprint_ignores_header_values_as_it_ignores_env_values() -> None:
    first = McpServerConfig(name="s", url="http://127.0.0.1:3900/mcp/", headers={"X-A": "one"})
    second = McpServerConfig(name="s", url="http://127.0.0.1:3900/mcp/", headers={"X-A": "two"})
    assert mcp_api._fingerprint(first) == mcp_api._fingerprint(second)


def test_the_module_add_function_is_the_route_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="https"):
        mcp_api.add(
            tmp_path,
            "x",
            "",
            [],
            {},
            url="http://example.com/mcp",
            headers={"Authorization": "Bearer t"},
        )
