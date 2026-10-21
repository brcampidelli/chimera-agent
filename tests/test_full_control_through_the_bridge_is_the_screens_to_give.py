"""Full control through the bridge: what it adds, what it never adds, and who may switch it on.

The owner's decision of 2026-10-18: through the MCP bridge, Claude Code may do everything in the
running app — approve, export, delete, change the owner's settings, open any screen — while a third
switch on Settings > Security is on. Two things stay out even then: credentials, and widening who
can reach the app.

What each test would catch if the rule broke:

* with the switch off, a full-control id answers anything but what it answered before;
* a route in the full-control table that the app does not serve, or that is also in `ROUTES`;
* the bridge (or the owner's bearer token alone) turning the switch on;
* a newly reachable class (an owner's decision, a confirmed route, an owner's setting, a settings
  suggestion, opening a screen) still refused with the switch on;
* a credential or a reach-widening route or setting let through with the switch on;
* a call made on full control's authority leaving no audit entry, or one carrying content;
* switching it off not taking it away at the very next call;
* an approval — the kind full control can now give — running what the kernel BLOCKs;
* a navigation target outside the app's own screens accepted, or the screens drifting from the
  desktop's list.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import screen_credential as sc
from chimera.api.bridge_discovery import read_discovery
from chimera.api.bridge_full_control import (
    ACTION_EVENT,
    NAVIGATION_VIEWS,
    UiEvents,
    parse_target,
    ui_event_frames,
)
from chimera.api.bridge_routes import (
    FULL_CONTROL_NEVER,
    FULL_CONTROL_NEVER_SETTINGS,
    FULL_CONTROL_ROUTES,
    FULL_CONTROL_SETTING,
    OWNER_DECISION_ROUTES,
    ROUTES,
)
from chimera.api.desktop_bridge import asgi_call
from chimera.config import get_settings
from chimera.governance import setting_suggestions
from chimera.governance.governed_tool import GovernedTool
from chimera.governance.kernel import TrustKernel
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from chimera.server.desktop_mcp import DesktopMCP
from chimera.tools.base import Tool

URL = "http://127.0.0.1:65031"
ROOT = Path(__file__).resolve().parent.parent


def _app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    full_control: bool,
    token: str = "",
    screen: sc.ScreenCredential | None = None,
) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", token)
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    monkeypatch.setenv(FULL_CONTROL_SETTING, "true" if full_control else "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)), screen=screen)
    app.state.desktop_bridge.attach(URL)
    return app


def _call(client: TestClient, app: Any, route: str, **kw: Any) -> Any:
    return client.post(
        "/api/bridge/call",
        json={"route": route, **kw},
        headers={"Authorization": f"Bearer {app.state.desktop_bridge.token}"},
    )


def _audit(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "home" / "audit.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [e for e in (json.loads(line) for line in lines if line.strip()) if e["type"] == ACTION_EVENT]


# ---- the table ----------------------------------------------------------------------------------


def test_the_full_control_table_is_served_by_the_app_and_apart_from_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=False)
    served = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}
    for route_id, route in FULL_CONTROL_ROUTES.items():
        assert (route.method, route.path) in served, route_id
        assert route.tier == "full_control"
    assert not set(FULL_CONTROL_ROUTES) & set(ROUTES)
    assert not set(FULL_CONTROL_NEVER) & set(FULL_CONTROL_ROUTES)
    # Every owner's decision becomes reachable but the two the owner excluded, which are refused
    # with their own sentence.
    left = set(OWNER_DECISION_ROUTES) - set(FULL_CONTROL_ROUTES)
    assert left == {"settings.exec", "settings.messaging_start"}
    assert left <= set(FULL_CONTROL_NEVER)


def test_no_credential_or_reach_route_is_in_the_full_control_table() -> None:
    paths = [(r.method, r.path) for r in FULL_CONTROL_ROUTES.values()]
    for method, path in paths:
        assert not path.startswith(
            ("/api/security/", "/api/devices", "/api/config", "/api/messaging", "/api/fs/exec")
        ), path
        assert "/share" not in path and "oauth" not in path and "pool" not in path, path
        assert (method, path) != ("POST", "/api/mcp"), path


def test_the_never_settings_hold_the_credential_and_reach_sets() -> None:
    for key in (
        FULL_CONTROL_SETTING,
        "CHIMERA_DESKTOP_BRIDGE",
        "CHIMERA_DESKTOP_BRIDGE_FULL",
        "CHIMERA_SHARING",
        "CHIMERA_SHARE_EXPIRY_HOURS",
        "CHIMERA_APP_MESSAGING",
        "CHIMERA_DISCORD_ALLOWED_USERS",
        "CHIMERA_KEY_VAULT",
        "CHIMERA_AGENT_READS_OWN_ENV",
        "CHIMERA_API_BASE",
    ):
        assert key in FULL_CONTROL_NEVER_SETTINGS, key


# ---- off: exactly as before ---------------------------------------------------------------------


def test_with_the_switch_off_the_full_control_ids_answer_as_they_always_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=False)
    found = read_discovery(app.state.desktop_bridge.path)
    assert found is not None and "full_control" not in found
    with TestClient(app) as client:
        assert client.get(
            "/api/bridge/status",
            headers={"Authorization": f"Bearer {app.state.desktop_bridge.token}"},
        ).json()["tier"] == "full"
        grant = _call(client, app, "settings.folder_grant", body={"path": str(tmp_path)})
        assert grant.status_code == 403
        assert grant.json()["detail"] == OWNER_DECISION_ROUTES["settings.folder_grant"]
        for route_id in ("design.delete", "ui.navigate", "storage.cache_clear"):
            assert _call(client, app, route_id, params={"design_id": "x"}).status_code == 404
    assert _audit(tmp_path) == []


# ---- the switch ---------------------------------------------------------------------------------


def test_the_bridge_cannot_flip_the_switch_at_any_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for on in (False, True):
        app = _app(tmp_path, monkeypatch, full_control=on)
        with TestClient(app) as client:
            edit = _call(client, app, "settings.edit", body={FULL_CONTROL_SETTING: "true"})
            assert edit.status_code == 403 and FULL_CONTROL_SETTING in edit.json()["detail"]
            # Not a setting the Settings screen writes at all.
            assert client.patch("/api/config", json={FULL_CONTROL_SETTING: "true"}).status_code == 400
            # The switch's own route refuses a request the bridge forwarded, either direction.
            for value in (True, False):
                status, raw = client.portal.call(  # type: ignore[union-attr]
                    lambda v=value, a=app: asgi_call(
                        a, "POST", "/api/security/bridge-full-control", body={"on": v},
                        full_control=True,
                    )
                )
                assert status == 403, raw
        assert get_settings().bridge_full_control is on
        get_settings.cache_clear()


def test_only_the_screen_turns_it_on_and_any_owner_credential_turns_it_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "owner-bearer-token-0123456789"
    screen = sc.for_mode("app", server_token=token)
    assert screen.value
    app = _app(tmp_path, monkeypatch, full_control=False, token=token, screen=screen)
    bearer = {"authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        refused = client.post("/api/security/bridge-full-control", json={"on": True}, headers=bearer)
        assert refused.status_code == 403 and "screen" in refused.json()["detail"]
        wrong = client.post(
            "/api/security/bridge-full-control", json={"on": True},
            headers={**bearer, sc.HEADER_NAME: token},
        )
        assert wrong.status_code == 403
        assert app.state.desktop_bridge.tier() == "full"

        on = client.post(
            "/api/security/bridge-full-control", json={"on": True},
            headers={**bearer, sc.HEADER_NAME: screen.value},
        )
        assert on.status_code == 200, on.text
        assert on.json()["on"] is True and on.json()["active"] is True
        assert app.state.desktop_bridge.tier() == "full_control"
        found = read_discovery(app.state.desktop_bridge.path)
        assert found is not None and found.get("full_control") is True
        assert f"{FULL_CONTROL_SETTING}=true" in (tmp_path / ".env").read_text(encoding="utf-8")

        off = client.post("/api/security/bridge-full-control", json={"on": False}, headers=bearer)
        assert off.status_code == 200 and off.json()["on"] is False
        assert app.state.desktop_bridge.tier() == "full"
        found = read_discovery(app.state.desktop_bridge.path)
        assert found is not None and "full_control" not in found
        actions = client.get("/api/security/bridge-full-control", headers=bearer).json()["actions"]
    switches = [a for a in actions if a["event"] == "bridge.full_control.switch"]
    assert [a["on"] for a in switches] == [False, True]  # newest first
    assert [a["via"] for a in switches] == ["owner", "screen"]


def test_it_needs_the_two_switches_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "owner-bearer-token-0123456789"
    screen = sc.for_mode("app", server_token=token)
    assert screen.value
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "false")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", token)
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv(FULL_CONTROL_SETTING, "true")  # set, but meaningless without Full control
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)), screen=screen)
    app.state.desktop_bridge.attach(URL)
    assert app.state.desktop_bridge.tier() == "operate"
    with TestClient(app) as client:
        got = client.post(
            "/api/security/bridge-full-control", json={"on": True},
            headers={"authorization": f"Bearer {token}", sc.HEADER_NAME: screen.value},
        )
    assert got.status_code == 409


# ---- on: each newly reachable class -------------------------------------------------------------


def test_with_full_control_the_owners_decisions_are_reachable_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    app = _app(tmp_path, monkeypatch, full_control=True)
    with TestClient(app) as client:
        grant = _call(
            client, app, "settings.folder_grant", body={"path": str(folder), "shell_granted": True}
        )
        assert grant.status_code == 200 and grant.json()["status"] == 200, grant.text
        rules = _call(client, app, "approve.rules_list")
        assert rules.json()["status"] == 200 and rules.json()["data"] == []
        rejected = _call(client, app, "approve.reject_all", body={"run_id": "r1", "reason": "no"})
        assert rejected.json()["status"] == 200, rejected.text
        saved = _call(
            client, app, "settings.agent_upsert",
            body={"id": "reviewer", "name": "Q", "instructions": "private-instructions-text"},
        )
        assert saved.json()["status"] == 200, saved.text
        projects = client.get("/api/code/workspaces").json()
        agents = client.get("/api/agents/registry").json()
    assert any(row.get("shell_granted") for row in projects)
    assert [a["id"] for a in agents] == ["reviewer"]

    entries = _audit(tmp_path)
    assert [e["route"] for e in entries] == [
        "settings.folder_grant", "approve.rules_list", "approve.reject_all", "settings.agent_upsert",
    ]
    assert all(e["via"] == "bridge" and e["scope"] == "full_control" for e in entries)
    raw = (tmp_path / "home" / "audit.jsonl").read_text(encoding="utf-8")
    assert "private-instructions-text" not in raw and str(folder) not in raw


def test_with_full_control_the_confirmed_routes_are_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    with TestClient(app) as client:
        made = client.post("/api/design", json={"title": "Poster"})
        assert made.status_code == 200, made.text
        design_id = made.json()["id"]
        exported = _call(
            client, app, "design.export", params={"design_id": design_id},
            body={"format": "html", "confirm": True},
        )
        assert exported.json()["status"] == 200, exported.text
        rotated = _call(client, app, "storage.logs_rotate", body={"confirm": True})
        assert rotated.json()["status"] == 200, rotated.text
        # The app's own refusal still stands: without the confirm, it is a 400 from the route.
        unconfirmed = _call(client, app, "design.delete", params={"design_id": design_id}, body={})
        assert unconfirmed.json()["status"] == 400
        deleted = _call(
            client, app, "design.delete", params={"design_id": design_id}, body={"confirm": True}
        )
        assert deleted.json()["status"] == 200, deleted.text
        assert client.get(f"/api/design/{design_id}").status_code == 404
    routes = [e["route"] for e in _audit(tmp_path)]
    assert routes == ["design.export", "storage.logs_rotate", "design.delete", "design.delete"]
    assert all(e["ids"] == {"design_id": design_id} for e in _audit(tmp_path) if e["route"].startswith("design"))


def test_with_full_control_the_owners_settings_are_written_not_suggested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    for name in ("CHIMERA_REACH", "CHIMERA_APP_CRON", "CHIMERA_CACHE"):
        monkeypatch.setenv(name, "")  # owned, so the writes below are undone at teardown
        monkeypatch.delenv(name)
    get_settings.cache_clear()
    with TestClient(app) as client:
        guard = _call(client, app, "settings.edit", body={"CHIMERA_REACH": "read_only"})
        assert guard.json()["status"] == 200, guard.text
        cron = _call(client, app, "settings.edit", body={"CHIMERA_APP_CRON": "false"})
        assert cron.json()["status"] == 200, cron.text
        assert cron.json()["data"].get("suggestion") is None
        # A plain setting the full tier already wrote does not use full control.
        plain = _call(client, app, "settings.edit", body={"CHIMERA_CACHE": "true"})
        assert plain.json()["status"] == 200, plain.text
    get_settings.cache_clear()
    assert get_settings().reach == "read_only"
    assert get_settings().app_cron is False
    entries = _audit(tmp_path)
    assert [e["ids"]["keys"] for e in entries] == [["CHIMERA_REACH"], ["CHIMERA_APP_CRON"]]
    assert "read_only" not in json.dumps(entries)


def test_with_full_control_a_settings_suggestion_is_answered_through_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    home = tmp_path / "home"
    request_id = setting_suggestions.suggest(
        home,
        [setting_suggestions.Change("CHIMERA_APP_CRON", "true", "false")],
        suggested_by="desktop_bridge",
    )
    with TestClient(app) as client:
        answered = _call(
            client, app, "approve.approval", params={"request_id": request_id},
            body={"approved": False},
        )
    assert answered.status_code == 200, answered.text
    assert answered.json()["data"]["ok"] is True and answered.json()["data"]["outcome"] == "refused"
    assert [e["ids"] for e in _audit(tmp_path)] == [{"request_id": request_id}]


def test_without_full_control_the_same_suggestion_stays_the_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=False)
    request_id = setting_suggestions.suggest(
        tmp_path / "home",
        [setting_suggestions.Change("CHIMERA_APP_CRON", "true", "false")],
        suggested_by="desktop_bridge",
    )
    with TestClient(app) as client:
        answered = _call(
            client, app, "approve.approval", params={"request_id": request_id},
            body={"approved": False},
        )
    assert answered.status_code == 403


def test_with_full_control_the_bridge_opens_a_screen_and_only_a_known_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    bus: UiEvents = app.state.ui_events
    with TestClient(app) as client:
        opened = _call(client, app, "ui.navigate", body={"target": "#/settings?tab=security"})
        assert opened.json()["status"] == 200, opened.text
        assert opened.json()["data"]["target"] == "#/settings?tab=security"
        for bad in ("https://example.com/", "#/nowhere", "#/settings/../code", "javascript:alert(1)",
                    "#/settings?tab=a#b", "//evil.example/#/code"):
            refused = _call(client, app, "ui.navigate", body={"target": bad})
            assert refused.json()["status"] == 400, bad
        # Not from the screen, nor from the bridge below full control: the route's own lock.
        assert client.post("/api/ui/navigate", json={"target": "#/code"}).status_code == 403
    events = bus.after(0)
    assert [e["data"]["target"] for e in events] == ["#/settings?tab=security"]
    assert _audit(tmp_path)[0]["ids"] == {"view": "settings"}


# ---- on: what stays out -------------------------------------------------------------------------


@pytest.mark.parametrize("route_id", sorted(FULL_CONTROL_NEVER))
def test_with_full_control_credentials_and_reach_stay_refused_with_why(
    route_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    with TestClient(app) as client:
        refused = _call(client, app, route_id, params={"platform": "discord"}, body={"command": "x"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == FULL_CONTROL_NEVER[route_id]
    assert _audit(tmp_path) == []


@pytest.mark.parametrize(
    "key",
    [
        "OPENROUTER_API_KEY",
        "CHIMERA_SERVER_TOKEN",
        "CHIMERA_APPROVAL_WEBHOOK",
        "CHIMERA_SHARING",
        "CHIMERA_DISCORD_ALLOWED_USERS",
        "CHIMERA_APP_MESSAGING",
        "CHIMERA_DESKTOP_BRIDGE",
        "CHIMERA_DESKTOP_BRIDGE_FULL",
        "CHIMERA_KEY_VAULT",
        "CHIMERA_AGENT_READS_OWN_ENV",
        "CHIMERA_API_BASE",
    ],
)
def test_with_full_control_no_credential_or_reach_setting_is_written(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    with TestClient(app) as client:
        refused = _call(client, app, "settings.edit", body={key: "x", "CHIMERA_REACH": "read_only"})
    assert refused.status_code == 403 and key in refused.json()["detail"]
    get_settings.cache_clear()
    assert get_settings().reach != "read_only"  # refused whole, nothing written


def test_with_full_control_credential_files_stay_out_of_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-not-for-the-bridge\n", encoding="utf-8")
    with TestClient(app) as client:
        read = _call(client, app, "files.read", params={"path": ".env", "workspace": str(tmp_path)})
        grant = _call(
            client, app, "settings.folder_grant",
            body={"path": str(tmp_path / "home"), "shell_granted": True},
        )
    assert read.status_code == 403
    assert grant.status_code == 403  # commands in the app's own data folder: refused as before


def test_turning_it_off_takes_it_away_at_the_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    with TestClient(app) as client:
        assert _call(client, app, "ui.navigate", body={"target": "#/code"}).json()["status"] == 200
        # Off as the setting reads it, before anything rewrote the discovery file.
        monkeypatch.setenv(FULL_CONTROL_SETTING, "false")
        get_settings.cache_clear()
        assert _call(client, app, "ui.navigate", body={"target": "#/code"}).status_code == 404
        grant = _call(client, app, "settings.folder_grant", body={"path": str(tmp_path)})
        assert grant.status_code == 403
        assert grant.json()["detail"] == OWNER_DECISION_ROUTES["settings.folder_grant"]


# ---- the kernel floor ---------------------------------------------------------------------------


class _Shell(Tool):
    name = "run_shell"
    description = "stand-in shell that records whether it ran"
    parameters: dict[str, Any] = {"type": "object", "properties": {"command": {"type": "string"}}}

    def __init__(self) -> None:
        self.ran: list[str] = []

    def run(self, **kwargs: Any) -> str:
        self.ran.append(str(kwargs.get("command")))
        return "ran"


def test_an_approval_never_overrides_a_governance_block() -> None:
    """Full control can answer approvals; an approver that says yes to everything is the most it can
    be. A kernel BLOCK never reaches it, so the command does not run."""
    shell = _Shell()
    asked: list[str] = []

    def yes(_verdict: Any, shown: str) -> bool:
        asked.append(shown)
        return True

    governed = GovernedTool(shell, TrustKernel(), approve=yes)
    out = governed.run(command="rm -rf /")
    assert "BLOCKED" in out
    assert shell.ran == [] and asked == []


def test_with_full_control_answering_a_question_that_is_not_waiting_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full_control=True)
    with TestClient(app) as client:
        answered = _call(
            client, app, "approve.approval", params={"request_id": "0123456789ab"},
            body={"approved": True},
        )
    assert answered.json()["data"] == {"ok": False}


# ---- navigation targets and the screen's stream -------------------------------------------------


def test_the_navigable_screens_are_the_desktops() -> None:
    rail = (ROOT / "apps/desktop/src/components/IconRail.tsx").read_text(encoding="utf-8")
    block = rail.split("export const VIEWS = [", 1)[1].split("]", 1)[0]
    views = set(re.findall(r'"(\w+)"', block))
    assert views, "the probe found no VIEWS"
    assert set(NAVIGATION_VIEWS) == views - {"maturity"}


def test_a_target_is_an_address_inside_the_app() -> None:
    assert parse_target("#/settings?tab=security") == ("settings", {"tab": "security"})
    assert parse_target(" #/code ") == ("code", {})
    for bad in (
        "settings", "/settings", "#settings", "#/maturity", "#/Settings", "#/settings?Tab=x",
        "#/settings?tab=a&tab=b", "#/settings?tab=%0A", "x" * 3000, "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            parse_target(bad)


def test_the_screen_stream_says_hello_then_what_was_asked() -> None:
    bus = UiEvents()
    bus.publish("navigate", {"target": "#/work"})  # before the screen connected: not replayed
    calls = {"n": 0}

    async def disconnected() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            bus.publish("navigate", {"target": "#/code"})
            return False
        return calls["n"] > 2

    async def frames() -> list[dict[str, str]]:
        return [f async for f in ui_event_frames(bus, -1, disconnected, poll=0)]

    got = asyncio.run(frames())
    assert got[0]["event"] == "hello" and json.loads(got[0]["data"]) == {"seq": 1}
    assert [json.loads(f["data"])["target"] for f in got[1:]] == ["#/code"]
    assert bus.listeners == 0


# ---- the MCP server -----------------------------------------------------------------------------


def _mcp(full_control: bool) -> tuple[DesktopMCP, list[Any]]:
    calls: list[Any] = []

    def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
        calls.append((method, url, body))
        return 200, {"route": "x", "status": 200, "data": {"ok": True}}

    found: dict[str, Any] = {"url": URL, "token": "t", "pid": 1, "version": "x", "full": True}
    if full_control:
        found["full_control"] = True
    return DesktopMCP(discover=lambda: found, http=http), calls


def test_the_mcp_server_lists_the_full_control_tools_only_while_it_is_on() -> None:
    off, _ = _mcp(False)
    names_off = {s["name"] for s in off.tool_specs()}
    assert not names_off & {"desktop_full_control", "desktop_navigate"}
    on, _ = _mcp(True)
    specs = {s["name"]: s for s in on.tool_specs()}
    assert {"desktop_full_control", "desktop_navigate"} <= set(specs)
    actions = set(specs["desktop_full_control"]["inputSchema"]["properties"]["action"]["enum"])
    assert actions == set(FULL_CONTROL_ROUTES)
    assert names_off == set(specs) - {"desktop_full_control", "desktop_navigate"}


def test_the_mcp_server_forwards_on_and_refuses_off_and_never() -> None:
    off, calls_off = _mcp(False)
    assert "Security" in off.dispatch("desktop_navigate", {"target": "#/code"})
    assert off.dispatch("desktop_design", {"action": "delete"}).startswith("Unknown action")
    assert off.dispatch("desktop_settings", {"action": "folder_grant"}) == (
        f"Refused: {OWNER_DECISION_ROUTES['settings.folder_grant']}"
    )
    assert calls_off == []

    on, calls = _mcp(True)
    on.dispatch("desktop_navigate", {"target": "#/settings?tab=security"})
    on.dispatch("desktop_full_control", {"action": "design.delete", "params": {"design_id": "d1"}})
    on.dispatch("desktop_design", {"action": "restore", "params": {"design_id": "d1"}})
    on.dispatch("desktop_settings", {"action": "folder_grant", "body": {"path": "p"}})
    sent = [c[2]["route"] for c in calls]
    assert sent == ["ui.navigate", "design.delete", "design.restore", "settings.folder_grant"]
    assert calls[0][2]["body"] == {"target": "#/settings?tab=security"}
    before = len(calls)
    assert on.dispatch("desktop_settings", {"action": "exec"}) == (
        f"Refused: {FULL_CONTROL_NEVER['settings.exec']}"
    )
    assert on.dispatch("desktop_full_control", {"action": "settings.messaging_start"}).startswith(
        "Refused:"
    )
    assert len(calls) == before


@pytest.mark.parametrize(
    ("action", "irreversible", "refused"),
    [
        ("click: button: Pay now on shop.example", True, True),
        ("google search: cheap flights", False, True),
        ("click: link: Catalog on shop.example", False, False),
    ],
)
def test_with_full_control_the_two_chrome_cards_stay_the_screens(
    action: str, irreversible: bool, refused: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Study 36, E4: the owner gave the bridge the approvals on purpose (18/10), except an
    irreversible action in their own Chrome and a Google search there — refused with their reason;
    every other card is still answered through the bridge as before."""
    from types import SimpleNamespace

    import chimera.governance.pending as pending_mod

    question = SimpleNamespace(id="0123456789ab", action=action, irreversible=irreversible, run_id="")
    monkeypatch.setattr(pending_mod, "pending", lambda _home: [question])
    app = _app(tmp_path, monkeypatch, full_control=True)
    with TestClient(app) as client:
        answered = _call(
            client, app, "approve.approval", params={"request_id": "0123456789ab"},
            body={"approved": True},
        )
    text = answered.text
    if refused:
        assert "never through the desktop bridge" in text
    else:
        assert "never through the desktop bridge" not in text
