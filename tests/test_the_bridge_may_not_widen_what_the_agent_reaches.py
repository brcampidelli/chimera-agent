"""Four doors that widen what the agent reaches are the owner's, at every bridge tier.

Until 2026-10-04 Full control could grant a folder its commands (``settings.folder_grant``), run a
command outside governance (``settings.exec``), start a messaging bot (``settings.messaging_start``)
and save an agent with its tool grants (``settings.agent_upsert``). The owner decided these are his,
in the app: each one is reach handed out in one call, by a client that may have read a poisoned
page, and turning Full control off afterwards comes after the reach was given.

Held here: each is refused at the operate tier and at Full control with the sentence that says
where the owner does it, nothing happens, the MCP server neither lists nor forwards them, the docs
the bridge advertises no longer promise them — and the owner's own app routes still serve.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import OWNER_DECISION_ROUTES, ROUTES
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from chimera.server.desktop_mcp import DesktopMCP

URL = "http://127.0.0.1:65008"
ROOT = Path(__file__).resolve().parent.parent

#: Each closed route: the app route the owner's screen still calls, and a body that would widen.
CLOSED: dict[str, tuple[str, str, dict[str, Any], dict[str, Any]]] = {
    "settings.folder_grant": ("PUT", "/api/code/workspaces/grant", {}, {"shell_granted": True}),
    "settings.exec": ("POST", "/api/fs/exec", {}, {"command": "echo widened"}),
    "settings.messaging_start": (
        "POST",
        "/api/messaging/{platform}/start",
        {"platform": "discord"},
        {},
    ),
    "settings.agent_upsert": (
        "PUT",
        "/api/agents/registry",
        {},
        {"id": "widened", "name": "W", "instructions": "x", "allowed_tools": ["run_shell"]},
    ),
}


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, full: bool, on: bool = True) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true" if on else "false")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _call(client: TestClient, app: Any, route: str, **kw: Any) -> Any:
    token = app.state.desktop_bridge.token or "no-token"
    return client.post(
        "/api/bridge/call",
        json={"route": route, **kw},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_the_four_are_exactly_the_closed_set_and_none_is_in_the_table() -> None:
    assert set(OWNER_DECISION_ROUTES) == set(CLOSED)
    assert not set(OWNER_DECISION_ROUTES) & set(ROUTES)


@pytest.mark.parametrize("full", [False, True], ids=["operate", "full"])
@pytest.mark.parametrize("route_id", sorted(CLOSED))
def test_each_is_refused_at_every_tier_with_where_the_owner_does_it(
    route_id: str, full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    _, _, params, body = CLOSED[route_id]
    if route_id == "settings.folder_grant":
        body = {**body, "path": str(folder)}
    app = _app(tmp_path, monkeypatch, full=full)

    with TestClient(app) as client:
        refused = _call(client, app, route_id, params=params, body=body)
        projects = client.get("/api/code/workspaces").json()
        agents = client.get("/api/agents/registry").json()
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == OWNER_DECISION_ROUTES[route_id]
    assert "owner's decision" in refused.json()["detail"]
    assert "never through the bridge" in refused.json()["detail"]
    # Nothing happened: no folder granted, no agent saved.
    assert not any(row.get("shell_granted") for row in projects)
    assert agents == []
    get_settings.cache_clear()


def test_with_the_switch_off_or_the_wrong_token_they_answer_as_every_route_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked after the token, like an unknown route: the sentence is for a client the owner let
    in, not a map of the app for anyone who asks."""
    off = _app(tmp_path, monkeypatch, full=True, on=False)
    with TestClient(off) as client:
        assert _call(client, off, "settings.exec", body={}).status_code == 403
        assert "bridge is off" in _call(client, off, "settings.exec", body={}).json()["detail"]
    on = _app(tmp_path, monkeypatch, full=True)
    with TestClient(on) as client:
        wrong = client.post(
            "/api/bridge/call",
            json={"route": "settings.exec", "body": {}},
            headers={"Authorization": "Bearer wrong"},
        )
    assert wrong.status_code == 401


def test_the_owners_own_routes_still_serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch, full=True)
    served = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}
    for route_id, (method, path, _, _) in CLOSED.items():
        assert (method, path) in served, route_id

    folder = tmp_path / "proj"
    folder.mkdir()
    with TestClient(app) as client:
        granted = client.put(
            "/api/code/workspaces/grant", json={"path": str(folder), "shell_granted": True}
        )
        saved = client.put(
            "/api/agents/registry", json={"id": "reviewer", "name": "Q", "instructions": "x"}
        )
    assert granted.status_code == 200 and granted.json()[0]["shell_granted"] is True
    assert saved.status_code == 200 and [a["id"] for a in saved.json()] == ["reviewer"]


def test_the_mcp_server_neither_lists_nor_forwards_them() -> None:
    calls: list[Any] = []

    def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
        calls.append((method, url, body))
        return 200, {"route": "x", "status": 200, "data": {}}

    found = {"url": "http://127.0.0.1:65008", "token": "t", "pid": 1, "version": "x", "full": True}
    mcp = DesktopMCP(discover=lambda: found, http=http)
    listed = {
        f"{spec['name'].removeprefix('desktop_')}.{action}"
        for spec in mcp.tool_specs()
        for action in spec["inputSchema"]["properties"].get("action", {}).get("enum", [])
    }
    assert not listed & set(CLOSED)
    for route_id in CLOSED:
        area, _, action = route_id.partition(".")
        text = mcp.dispatch(f"desktop_{area}", {"action": action, "body": {}})
        assert text == f"Refused: {OWNER_DECISION_ROUTES[route_id]}"
    assert calls == []


def test_what_the_bridge_advertises_no_longer_promises_them() -> None:
    mcp = DesktopMCP(
        discover=lambda: {"url": "u", "token": "t", "pid": 1, "version": "x", "full": True},
        http=lambda *a: (200, None),
    )
    settings_tool = next(s for s in mcp.tool_specs() if s["name"] == "desktop_settings")
    described = json.dumps(settings_tool)
    assert "run a command" not in described.lower().replace("running a command", "")
    assert "owner's, in the app" in described
    docs = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    assert "run a\ncommand in the Runner" not in docs and "run a command in the Runner" not in docs
