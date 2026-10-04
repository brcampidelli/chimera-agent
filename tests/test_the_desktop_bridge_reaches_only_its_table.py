"""What the bridge lets a client do: its route table, its two tiers, and never a credential.

The server side of `chimera mcp desktop` (`chimera/api/desktop_bridge.py`,
`chimera/api/bridge_routes.py`). Held here:

* every table entry is a real app route, and a call is served by that route's own handler;
* full-control routes answer 403 until the second switch is on — server-side, whatever the client
  lists;
* in the operate tier a body cannot widen a run (posture, host exec, verify commands, an external
  agent, auto-approval), and a run is sent the owner's configured posture;
* settings edits refuse credentials and the bridge's own switches, full control or not;
* no response carries a key, a masked key's last characters, or the bridge token;
* a streamed call that stops for an approval answers promptly — it does not hang — and the job
  carries on and can be read to its end.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import Body
from fastapi.testclient import TestClient
from sse_starlette.sse import EventSourceResponse

from chimera.api import build_api_app
from chimera.api.bridge_routes import (
    FULL_ONLY_BODY_KEYS,
    ROUTES,
    BridgeRoute,
    full_only_keys_in,
    is_secret_setting,
    scrub,
)
from chimera.api.code_api import CodeSeams
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65003"
FAKE_KEY = "sk-or-v1-" + "a1b2c3d4e5f6" * 4


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, full: bool = False, **env: str) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _call(client: TestClient, app: Any, route: str, **kw: Any) -> Any:
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    return client.post("/api/bridge/call", json={"route": route, **kw}, headers=headers)


def _ask(home: Path, request_id: str = "q1") -> Path:
    directory = home / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    question = directory / f"{request_id}.ask.json"
    question.write_text(
        json.dumps(
            {
                "id": request_id,
                "action": "run_shell: rm -rf build",
                "reason": "destructive",
                "asked_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    return directory


# ---- the table ----------------------------------------------------------------------------------


def _bare_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    return build_api_app(lambda: ChatSession(cast(SupportsRun, None)))


def test_every_route_in_the_table_is_a_route_the_app_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _bare_app(tmp_path, monkeypatch)
    served = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}

    missing = [rid for rid, r in ROUTES.items() if (r.method, r.path) not in served]
    assert missing == []


def test_every_route_whose_body_is_a_run_is_flagged_so_the_posture_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run-shaped body the table forgot to flag would reach the handler with no posture — and an
    absent posture means nothing denied and no pause, wider than anything the owner chose."""
    app = _bare_app(tmp_path, monkeypatch)
    seams_paths = set()
    for r in app.routes:
        for param in getattr(getattr(r, "dependant", None), "body_params", []):
            kind = getattr(getattr(param, "field_info", None), "annotation", None)
            if isinstance(kind, type) and issubclass(kind, CodeSeams):
                seams_paths |= {(m, r.path) for m in r.methods}  # type: ignore[attr-defined]

    in_table = {(r.method, r.path) for r in ROUTES.values()}
    assert seams_paths & in_table, "found no run-shaped route at all: the probe is broken"
    unflagged = [
        rid for rid, r in ROUTES.items() if (r.method, r.path) in seams_paths and not r.seams
    ]
    assert unflagged == []


def test_no_credential_route_is_in_the_table_at_any_tier() -> None:
    paths = {r.path for r in ROUTES.values()}
    for forbidden in (
        "/api/config/pool/{provider}",
        "/api/config/pool/{provider}/{index}",
        "/api/config/test",
        "/api/code/sessions/{session_id}/share",
        "/api/code/sessions/{session_id}/shares",
        "/api/code/share/network",
        "/api/attachments",
        "/api/code/revert/{token}",
    ):
        assert forbidden not in paths
    assert ("POST", "/api/mcp") not in {(r.method, r.path) for r in ROUTES.values()}  # env values


def test_answering_approvals_and_editing_settings_are_full_control_only() -> None:
    full = {rid for rid, r in ROUTES.items() if r.tier == "full"}
    for rid, r in ROUTES.items():
        if r.path.startswith("/api/approvals/") or r.path.endswith(
            ("/approve", "/deny", "/respond")
        ):
            assert rid in full, rid
        if r.method != "GET" and r.path in {"/api/config", "/api/instructions", "/api/fs/exec"}:
            assert rid in full, rid
    assert ROUTES["approvals.list"].tier == "operate"  # reading them is not answering them


# ---- tiers, server-side -------------------------------------------------------------------------


def test_an_operate_route_is_served_by_the_apps_own_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    with TestClient(app) as client:
        added = _call(client, app, "projects.add", body={"path": str(project), "alias": "demo"})
        listed = _call(client, app, "projects.list")
    assert added.status_code == 200 and added.json()["status"] == 200
    # Path and name: the row also carries the folder's grant, pin and recency (study 29, P4.3),
    # which the tests under "folder grants" below hold on their own.
    row = listed.json()["data"][0]
    assert len(listed.json()["data"]) == 1
    assert (row["path"], row["alias"], row["shell_granted"]) == (str(project), "demo", False)


def test_approving_is_refused_server_side_without_full_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    directory = _ask(tmp_path / "home")

    with TestClient(app) as client:
        listed = _call(client, app, "approvals.list")
        refused = _call(
            client, app, "approve.approval", params={"request_id": "q1"}, body={"approved": True}
        )
    assert [q["id"] for q in listed.json()["data"]] == ["q1"]
    assert refused.status_code == 403
    assert not (directory / "q1.answer.json").exists()


def test_with_full_control_the_approval_is_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True)
    directory = _ask(tmp_path / "home")

    with TestClient(app) as client:
        answered = _call(
            client, app, "approve.approval", params={"request_id": "q1"}, body={"approved": False}
        )
    assert answered.status_code == 200 and answered.json()["data"] == {"ok": True}
    assert (
        json.loads((directory / "q1.answer.json").read_text(encoding="utf-8"))["approved"] is False
    )


def test_every_full_route_is_403_without_full_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        for rid, route in ROUTES.items():
            if route.tier != "full":
                continue
            params = {name: "x" for name in __import__("re").findall(r"{(\w+)}", route.path)}
            assert _call(client, app, rid, params=params, body={}).status_code == 403, rid


def _pending_bundle(home: Path, name: str = "stranger") -> Path:
    """An installed, not yet switched-on bundle, as `install` leaves one — without a network."""
    from chimera.skills import bundles

    root = bundles.bundles_root(home) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\nObey me.\n", encoding="utf-8")
    record = {"name": name, "description": "Third-party text.", "status": "pending"}
    (root / "bundle.json").write_text(json.dumps(record), encoding="utf-8")
    return root / "bundle.json"


@pytest.mark.parametrize(
    "body", [{"status": "active"}, {}, {"enabled": True}, {"status": "inactive", "extra": 1}]
)
def test_operate_cannot_switch_a_bundle_on_into_every_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    """A bundle switched on reaches every run's prompt; an operate client is not the owner. `{}`
    and the old `{enabled}` shape are here because the route's model defaults status to active."""
    from chimera.skills import bundles

    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    record = _pending_bundle(home)
    with TestClient(app) as client:
        refused = _call(client, app, "skills.bundle_status", params={"name": "stranger"}, body=body)
    assert refused.status_code == 403
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "pending"
    assert bundles.prompt_block(home) == ""


def test_operate_may_switch_a_bundle_off_and_full_control_may_switch_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.skills import bundles

    home = tmp_path / "home"
    record = _pending_bundle(home)
    app = _app(tmp_path, monkeypatch, full=True)
    with TestClient(app) as client:
        # Full control switches it on through the approval route (feat/skill-upload), not through
        # `skills.bundle_status`: that route sits in `desktop_skills`, a tool the operate tier
        # lists, so it only ever switches off — for every tier.
        through_skills = _call(
            client,
            app,
            "skills.bundle_status",
            params={"name": "stranger"},
            body={"status": "active"},
        )
        assert through_skills.status_code == 403
        assert bundles.prompt_block(home) == ""
        on = _call(
            client,
            app,
            "approve.skill_bundle",
            params={"name": "stranger"},
            body={"status": "active"},
        )
    assert on.status_code == 200 and on.json()["status"] == 200
    assert "stranger" in bundles.prompt_block(home)

    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        off = _call(
            client,
            app,
            "skills.bundle_status",
            params={"name": "stranger"},
            body={"status": "inactive"},
        )
    assert off.status_code == 200 and off.json()["status"] == 200
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "inactive"
    assert bundles.prompt_block(home) == ""


def test_switching_on_somebody_elses_skill_needs_full_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installing lands a skill pending; switching it on is the approval. If the operate tier could
    flip that switch, a client could install a stranger's instructions and enable them in two
    calls, and "pending until the owner turns it on" would hold only for the owner's own screen."""
    skill = tmp_path / "home" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\nbody", encoding="utf-8")
    record = skill / "bundle.json"
    record.write_text(json.dumps({"name": "demo", "status": "pending"}), encoding="utf-8")

    operate = _app(tmp_path, monkeypatch)
    with TestClient(operate) as client:
        refused = _call(
            client, operate, "approve.skill_bundle", params={"name": "demo"}, body={"status": "active"}
        )
    assert refused.status_code == 403
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "pending"

    full = _app(tmp_path, monkeypatch, full=True)
    with TestClient(full) as client:
        switched = _call(
            client, full, "approve.skill_bundle", params={"name": "demo"}, body={"status": "active"}
        )
    assert switched.status_code == 200
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "active"


def test_a_drive_name_reaches_no_skill_bundle_through_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`:` passes the bridge's path-parameter check, and on Windows `skills / "C:"` IS the skills
    directory — so `skills.bundle_delete {name: "C:"}` deleted every installed skill from the
    operate tier. The bundle-name rule refuses it below the bridge, for every caller."""
    skill = tmp_path / "home" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\nbody", encoding="utf-8")
    (skill / "bundle.json").write_text(json.dumps({"name": "demo", "status": "pending"}), encoding="utf-8")

    full = _app(tmp_path, monkeypatch, full=True)
    with TestClient(full) as client:
        deleted = _call(client, full, "skills.bundle_delete", params={"name": "C:"})
        switched = _call(
            client, full, "approve.skill_bundle", params={"name": "C:"}, body={"status": "active"}
        )

    # The bridge answers 200 with the app's own status inside — the app's answer is the refusal.
    assert deleted.status_code == 200 and deleted.json()["status"] == 404
    assert switched.status_code == 200 and switched.json()["status"] == 404
    assert (skill / "SKILL.md").is_file()
    assert json.loads((skill / "bundle.json").read_text(encoding="utf-8"))["status"] == "pending"


def test_an_unknown_route_and_a_traversing_parameter_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert _call(client, app, "config.pool_add").status_code == 404
        for bad in ("../config", "a/b", "a\\b", "..", "x?y=1"):
            response = _call(client, app, "conversations.read", params={"session_id": bad})
            assert response.status_code == 400, bad
        # And the table is not reachable without the token.
        assert client.post("/api/bridge/call", json={"route": "nope"}).status_code == 401


# ---- widening and posture -----------------------------------------------------------------------


def _capture_route(app: Any, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    @app.post("/api/test/seams")
    def _seams(body: dict[str, Any] = Body(...)) -> dict[str, bool]:  # noqa: B008
        seen.append(body)
        return {"ok": True}

    monkeypatch.setitem(ROUTES, "test.seams", BridgeRoute("POST", "/api/test/seams", seams=True))
    return seen


def test_an_operate_run_gets_the_owners_posture_and_no_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    seen = _capture_route(app, monkeypatch)

    with TestClient(app) as client:
        _call(client, app, "test.seams", body={"task": "x"})
        _call(client, app, "test.seams", body={"task": "x", "posture": None})
    for body in seen:
        assert body["posture"] == {"reach": "workspace", "approval": "suspicious"}
        assert body["allow_host_exec"] is False


def test_the_configured_posture_is_what_an_operate_run_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, CHIMERA_REACH="read_only", CHIMERA_APPROVAL="always")
    seen = _capture_route(app, monkeypatch)

    with TestClient(app) as client:
        _call(client, app, "test.seams", body={"task": "x"})
    assert seen[0]["posture"] == {"reach": "read_only", "approval": "always"}


@pytest.mark.parametrize("key", sorted(FULL_ONLY_BODY_KEYS))
def test_an_operate_body_cannot_widen_the_run(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    seen = _capture_route(app, monkeypatch)
    value: Any = {"reach": "workspace_shell", "approval": "never"} if key == "posture" else "yes"

    with TestClient(app) as client:
        flat = _call(client, app, "test.seams", body={"task": "x", key: value})
        nested = _call(client, app, "kanban.add_card", body={"title": "t", "extra": [{key: value}]})
    assert flat.status_code == 403 and nested.status_code == 403
    assert seen == []


def test_with_full_control_the_client_may_set_the_posture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True)
    seen = _capture_route(app, monkeypatch)
    wide = {"reach": "workspace_shell", "approval": "never"}

    with TestClient(app) as client:
        _call(
            client, app, "test.seams", body={"task": "x", "posture": wide, "allow_host_exec": True}
        )
    assert seen[0]["posture"] == wide and seen[0]["allow_host_exec"] is True


# ---- folder grants (study 29, P4.3) ---------------------------------------------------------------


def _grant(home: Path, folder: Path) -> None:
    from chimera.core.code_projects import CodeProjectRegistry

    CodeProjectRegistry(home / "code_projects.json").set_grant(str(folder), True)


def test_an_operate_run_in_a_granted_folder_gets_its_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bridge sees the same grant the Code screen does — and reads it from the same record,
    so a grant made in the app is honoured here without the bridge being told anything."""
    app = _app(tmp_path, monkeypatch)
    seen = _capture_route(app, monkeypatch)
    granted, other = tmp_path / "granted", tmp_path / "other"
    granted.mkdir()
    other.mkdir()
    _grant(tmp_path / "home", granted)

    with TestClient(app) as client:
        _call(client, app, "test.seams", body={"task": "x", "workspace": str(granted)})
        _call(client, app, "test.seams", body={"task": "x", "workspace": str(other)})
    assert seen[0]["posture"]["reach"] == "workspace_shell" and seen[0]["allow_host_exec"] is True
    assert seen[1]["posture"]["reach"] == "workspace" and seen[1]["allow_host_exec"] is False


def test_a_folder_grant_does_not_lift_the_owners_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, CHIMERA_REACH="read_only")
    seen = _capture_route(app, monkeypatch)
    granted = tmp_path / "granted"
    granted.mkdir()
    _grant(tmp_path / "home", granted)

    with TestClient(app) as client:
        _call(client, app, "test.seams", body={"task": "x", "workspace": str(granted)})
    assert seen[0]["posture"]["reach"] == "read_only" and seen[0]["allow_host_exec"] is False


def test_pinning_is_operate_and_granting_is_the_owners_at_every_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pinning and hiding only narrow (hiding revokes); granting widens, so it is the owner's.

    Until 2026-10-04 granting was Full control's, and this test asserted that a full-control client
    could grant. The owner decided otherwise: a folder's command grant is the posture itself, and
    the bridge now refuses it at every tier (`bridge_routes.OWNER_DECISION_ROUTES`). The owner's
    own route still grants, and the bridge reads that grant (the tests above)."""
    folder = tmp_path / "proj"
    folder.mkdir()
    operate = _app(tmp_path, monkeypatch)
    with TestClient(operate) as client:
        pinned = _call(client, operate, "projects.flag", body={"path": str(folder), "pinned": True})
        refused = _call(
            client,
            operate,
            "settings.folder_grant",
            body={"path": str(folder), "shell_granted": True},
        )
    assert pinned.status_code == 200 and pinned.json()["data"][0]["pinned"] is True
    assert refused.status_code == 403

    full = _app(tmp_path, monkeypatch, full=True)
    with TestClient(full) as client:
        still_refused = _call(
            client,
            full,
            "settings.folder_grant",
            body={"path": str(folder), "shell_granted": True},
        )
        listed = _call(client, full, "projects.list")
        owner = client.put(
            "/api/code/workspaces/grant", json={"path": str(folder), "shell_granted": True}
        )
        after_owner = _call(client, full, "projects.list")
    assert still_refused.status_code == 403
    assert "owner's decision" in still_refused.json()["detail"]
    assert listed.json()["data"][0]["shell_granted"] is False
    assert owner.status_code == 200
    assert after_owner.json()["data"][0]["shell_granted"] is True


def test_the_widening_scan_reads_nested_bodies_and_ignores_empty_values() -> None:
    assert full_only_keys_in({"tasks": [{"task": "a", "verify": "pytest"}]}) == ["verify"]
    assert full_only_keys_in({"verify": None, "posture": None, "allow_host_exec": False}) == []


# ---- settings and credentials -------------------------------------------------------------------


def test_settings_edit_refuses_credentials_and_the_switches_even_with_full_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True, CHIMERA_SANDBOX_IMAGE="before:image")

    with TestClient(app) as client:
        for key in (
            "OPENROUTER_API_KEY",
            "CHIMERA_SERVER_TOKEN",
            "CHIMERA_APPROVAL_WEBHOOK",
            "CHIMERA_OPENROUTER_KEYS",
            "CHIMERA_DESKTOP_BRIDGE",
            "CHIMERA_DESKTOP_BRIDGE_FULL",
            "CHIMERA_TELEGRAM_BOT_TOKEN",
        ):
            assert _call(client, app, "settings.edit", body={key: "x"}).status_code == 403, key
        # A setting the bridge still writes. It was the default model until 2026-10-04, when the
        # model choices became the owner's to write and the bridge's only to suggest
        # (`tests/test_the_bridge_may_only_suggest_which_model_answers.py`).
        ok = _call(client, app, "settings.edit", body={"CHIMERA_SANDBOX_IMAGE": "after:image"})
    assert ok.status_code == 200 and ok.json()["status"] == 200
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CHIMERA_SANDBOX_IMAGE=after:image" in env
    assert "API_KEY" not in env and "BRIDGE" not in env
    get_settings.cache_clear()


def test_every_credential_the_settings_api_knows_is_a_secret_to_the_bridge() -> None:
    from chimera.api.config_api import _SECRET_KEYS

    assert all(is_secret_setting(k) for k in _SECRET_KEYS)
    assert not is_secret_setting("CHIMERA_DEFAULT_MODEL")
    assert not is_secret_setting("CHIMERA_REACH")


def test_no_response_carries_a_key_its_last_characters_or_the_bridge_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True, OPENROUTER_API_KEY=FAKE_KEY)
    token = app.state.desktop_bridge.token
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    # A fact that happens to contain both — what a conversation can end up holding.
    with TestClient(app) as client:
        _call(client, app, "memory.add", body={"content": f"key {FAKE_KEY} bridge {token}"})
        texts = [
            _call(client, app, "app.config").text,
            _call(client, app, "memory.search", params={"q": "key"}).text,
            client.get("/api/bridge/status", headers={"Authorization": f"Bearer {token}"}).text,
        ]
        raw_config = client.get("/api/config").json()  # the SPA's own view still has the hint
    assert any(p.get("hint") for p in raw_config["providers"] if isinstance(p, dict)), (
        "probe is broken"
    )
    for text in texts:
        assert FAKE_KEY not in text
        assert FAKE_KEY[-4:] not in text
        assert token not in text
    get_settings.cache_clear()


def test_scrub_drops_masked_hints_and_token_fields_but_keeps_advice() -> None:
    out = scrub(
        {
            "providers": [{"set": True, "hint": "…abcd"}],
            "share_token": "abc",
            "hint": "run ollama pull",
            "note": "t0ken-XYZ",
        },
        ["t0ken-XYZ"],
    )
    assert out == {"providers": [{"set": True}], "hint": "run ollama pull", "note": "[redacted]"}


# ---- streamed calls wait, but never forever -----------------------------------------------------


def test_a_run_that_stops_for_an_approval_answers_at_once_and_finishes_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    release = threading.Event()

    @app.post("/api/test/stream")
    async def _stream() -> EventSourceResponse:
        async def frames() -> Any:
            yield {"event": "session", "data": json.dumps({"session_id": "s1", "turn_id": "t1"})}
            yield {"event": "token", "data": json.dumps({"text": "looking"})}
            yield {
                "event": "approval",
                "data": json.dumps(
                    {"id": "q9", "action": "run_shell: rm -rf build", "reason": "destructive"}
                ),
            }
            while not release.is_set():
                await asyncio.sleep(0.02)
            yield {
                "event": "done",
                "data": json.dumps(
                    {"answer": "done it", "model": "m", "usd": 0.01, "system_sha": "abc"}
                ),
            }

        return EventSourceResponse(frames())

    monkeypatch.setitem(ROUTES, "test.stream", BridgeRoute("POST", "/api/test/stream", stream=True))
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        started = time.monotonic()
        first = _call(client, app, "test.stream", body={}, wait_seconds=30).json()["job"]
        assert time.monotonic() - started < 10, "the call sat out its whole wait"
        assert first["waiting_for_approval"] is True and first["done"] is False
        assert first["pending_approvals"][0]["action"] == "run_shell: rm -rf build"
        assert first["session_id"] == "s1" and first["text"] == "looking"

        release.set()
        later = client.get(
            f"/api/bridge/jobs/{first['job_id']}?wait_seconds=10", headers=headers
        ).json()
    assert later["done"] is True and later["waiting_for_approval"] is False
    assert later["result"]["answer"] == "done it" and later["result"]["system_sha"] == "abc"


def test_a_run_that_takes_longer_than_the_wait_returns_what_it_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)

    @app.post("/api/test/slow")
    async def _slow() -> EventSourceResponse:
        async def frames() -> Any:
            yield {"event": "token", "data": json.dumps({"text": "a"})}
            await asyncio.sleep(30)
            yield {"event": "done", "data": "{}"}  # pragma: no cover - the test ends first

        return EventSourceResponse(frames())

    monkeypatch.setitem(ROUTES, "test.slow", BridgeRoute("POST", "/api/test/slow", stream=True))
    with TestClient(app) as client:
        started = time.monotonic()
        job = _call(client, app, "test.slow", body={}, wait_seconds=0.5).json()["job"]
    assert time.monotonic() - started < 10
    assert job["done"] is False and job["waiting_for_approval"] is False and job["text"] == "a"


# ---- places: the app's own data and credential files ------------------------------------------


def test_a_workspace_on_or_around_the_apps_data_is_refused_at_every_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The approval answers live in the app's data directory. A file write — or an agent told to
    write — aimed there would answer an approval without the switch that allows answering."""
    app = _app(tmp_path, monkeypatch, full=True)
    home = tmp_path / "home"
    directory = _ask(home)

    with TestClient(app) as client:
        for place in (home, home / "approvals", tmp_path):  # the data, inside it, and around it
            wrote = _call(
                client,
                app,
                "files.write",
                body={
                    "workspace": str(place),
                    "path": "approvals/q1.answer.json",
                    "content": '{"approved": true}',
                },
            )
            sent = _call(
                client, app, "conversations.send", body={"message": "hi", "workspace": str(place)}
            )
            added = _call(client, app, "projects.add", body={"path": str(place)})
            assert (wrote.status_code, sent.status_code, added.status_code) == (403, 403, 403), (
                place
            )
    assert not (directory / "q1.answer.json").exists()


def test_a_file_in_the_apps_data_is_refused_when_the_call_names_no_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check above read only a workspace the call NAMED. A call that names none runs in the
    app's own folder, and this app's own folder (the test's `tmp_path`, like an app started from the
    home folder) contains its data: a plain `files.write` of the answer file answered the question,
    at the operate tier, with no workspace field for the check to see."""
    app = _app(tmp_path, monkeypatch)  # operate: no switch that allows answering
    home = tmp_path / "home"
    directory = _ask(home)

    with TestClient(app) as client:
        absolute = _call(
            client,
            app,
            "files.write",
            body={"path": str(directory / "q1.answer.json"), "content": '{"approved": true}'},
        )
        relative = _call(
            client,
            app,
            "files.write",
            body={"path": "home/approvals/q1.answer.json", "content": '{"approved": true}'},
        )
        read = _call(client, app, "files.read", params={"path": "home/approvals/q1.ask.json"})
        elsewhere = _call(
            client, app, "files.write", body={"path": "notes.txt", "content": "still fine"}
        )
    assert (absolute.status_code, relative.status_code, read.status_code) == (403, 403, 403)
    assert not (directory / "q1.answer.json").exists()
    assert elsewhere.status_code == 200 and elsewhere.json()["status"] == 200
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "still fine"


def test_credential_files_are_unreadable_unwritable_and_unsearchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True)
    project = tmp_path / "proj"
    (project / "sub").mkdir(parents=True)
    (project / ".env").write_text(f"OPENROUTER_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    (project / "sub" / "notes.txt").write_text(
        "OPENROUTER_API_KEY lives elsewhere\n", encoding="utf-8"
    )
    ws = str(project)

    with TestClient(app) as client:
        for path in (".env", "sub/../.env", ".env.local", "keys/id_rsa", "cert.pem"):
            assert (
                _call(client, app, "files.read", params={"workspace": ws, "path": path}).status_code
                == 403
            )
        assert (
            _call(
                client, app, "files.write", body={"workspace": ws, "path": ".env", "content": "X=1"}
            ).status_code
            == 403
        )
        assert (
            _call(
                client,
                app,
                "git.commit",
                body={"workspace": ws, "message": "m", "paths": ["a.py", ".env"]},
            ).status_code
            == 403
        )
        found = _call(
            client, app, "files.search", body={"workspace": ws, "query": "OPENROUTER_API_KEY"}
        )
        ordinary = _call(
            client, app, "files.read", params={"workspace": ws, "path": "sub/notes.txt"}
        )
    hits = found.json()["data"]["hits"]
    assert [h["path"] for h in hits] == ["sub/notes.txt"]
    assert FAKE_KEY not in found.text
    assert ordinary.json()["data"]["content"].startswith("OPENROUTER_API_KEY lives")
    assert (project / ".env").read_text(encoding="utf-8").startswith("OPENROUTER_API_KEY=")
