"""Four doors that widen what the agent reaches are the owner's, at every bridge tier.

Until 2026-10-04 Full control could grant a folder its commands (``settings.folder_grant``), run a
command outside governance (``settings.exec``), start a messaging bot (``settings.messaging_start``)
and save an agent with its tool grants (``settings.agent_upsert``). The owner decided these are his,
in the app: each one is reach handed out in one call, by a client that may have read a poisoned
page, and turning Full control off afterwards comes after the reach was given.

Held here: each is refused at the operate tier and at Full control with the sentence that says
where the owner does it, nothing happens, the MCP server neither lists nor forwards them, the docs
the bridge advertises no longer promise them — and the owner's own app routes still serve.

On 2026-10-15 four more joined them, from the project-scoped "always" approval: listing and revoking
remembered rules, creating one, and refusing every question of a run. A standing approval is reach
handed out once for every later call, so it is the owner's on the same terms. Creating one is not a
route of its own — it is ``remember: "project"`` on the approval answer — so it is also tested end
to end below: the bridge may answer, never remember.
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
    "approve.rules_list": ("GET", "/api/approval-rules", {}, {}),
    "approve.rules_revoke": ("DELETE", "/api/approval-rules/{rule_id}", {"rule_id": "r1"}, {}),
    "approve.remember_project": (
        "POST",
        "/api/approvals/{request_id}",
        {"request_id": "q1"},
        {"approved": True, "remember": "project"},
    ),
    "approve.reject_all": ("POST", "/api/approvals/reject-all", {}, {"run_id": "r", "reason": "x"}),
    "projects.worktrees_prune": (
        "POST",
        "/api/code/projects/worktrees/prune",
        {},
        {"project": "proj", "paths": ["proj"], "dry_run": False},
    ),
    # Plugins (plan B5, 2026-10-17): switched on, they reach every prompt and start servers.
    "plugins.enable": ("POST", "/api/plugins/{name}/enable", {"name": "p"}, {}),
    "plugins.add_mcp": ("POST", "/api/plugins/{name}/enable", {"name": "p"}, {"add_mcp": ["s"]}),
    "plugins.remove": ("DELETE", "/api/plugins/{name}", {"name": "p"}, {}),
    "plugins.marketplace_add": ("POST", "/api/plugins/marketplaces", {}, {"source": "github:a/b@x"}),
    "plugins.marketplace_remove": ("DELETE", "/api/plugins/marketplaces/{name}", {"name": "m"}, {}),
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


def test_a_design_route_that_takes_the_persons_confirm_is_not_on_the_bridge() -> None:
    """Restore, delete and export (Chimera Design, D2) refuse without `confirm: true`, and that flag
    is the person's answer on the canvas. A bridge client sending it would answer its own question,
    so the routes are absent at every tier — as the storage card's confirm routes are — and the MCP
    server neither lists nor forwards them."""
    for route_id in ("design.restore", "design.delete", "design.export"):
        assert route_id not in ROUTES and route_id not in OWNER_DECISION_ROUTES
    calls: list[Any] = []

    def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
        calls.append((method, url))
        return 200, {"route": "x", "status": 200, "data": {}}

    found = {"url": "http://127.0.0.1:65008", "token": "t", "pid": 1, "version": "x", "full": True}
    mcp = DesktopMCP(discover=lambda: found, http=http)
    design = next(s for s in mcp.tool_specs() if s["name"] == "desktop_design")
    actions = set(design["inputSchema"]["properties"]["action"]["enum"])
    assert not actions & {"restore", "delete", "export"}
    assert {"read", "ops", "propose"} <= actions


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
    assert not any(row.get("model") for row in projects)
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


def test_the_bridge_may_answer_a_question_but_never_remember_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`remember: "project"` rides on the ordinary answer, which Full control may send: the app
    route itself refuses it when the request came through the bridge, and no rule is written."""
    from chimera.governance.approval_rules import list_rules
    from chimera.governance.pending import _forget_code, _remember_code

    app = _app(tmp_path, monkeypatch, full=True)
    home = tmp_path / "home"
    directory = home / "approvals"
    directory.mkdir(parents=True)
    (directory / "q1.ask.json").write_text(
        json.dumps({"id": "q1", "action": "write_file: a.py", "reason": "review", "asked_at": 1.0,
                    "decision": "review", "run_id": "run-1"}),
        encoding="utf-8",
    )
    # Asked by this process, so the screen could approve it without a typed code.
    _remember_code("q1", "123456")
    try:
        with TestClient(app) as client:
            refused = _call(
                client, app, "approve.approval",
                params={"request_id": "q1"}, body={"approved": True, "remember": "project"},
            )
    finally:
        _forget_code("q1")
        get_settings.cache_clear()
    # The bridge forwarded the answer; the app route refused it inside the envelope.
    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == 403
    assert refused.json()["data"]["detail"] == "only the owner may remember approvals"
    assert list_rules(home) == []
    assert not (directory / "q1.answer.json").exists()
