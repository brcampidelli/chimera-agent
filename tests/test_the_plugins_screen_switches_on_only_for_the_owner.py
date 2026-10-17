"""Settings › Plugins over HTTP (plan B5): every route the screen calls, and what the bridge reaches.

The owner's decisions of 2026-10-17, held end to end here:

* a third party's plugin installs switched off (``pending``) through every door, the bridge's
  included, and nothing of it is loaded;
* switching it ON is the owner's: ``plugins.enable`` (and ``plugins.add_mcp``, the servers named on
  enabling) is refused at the operate tier AND at full control, and after the refusal the plugin is
  still pending — as are removing it and adding or forgetting a marketplace;
* an update shows its diff before anything changes, and applying it leaves the plugin held or
  pending, never on;
* a marketplace entry that is unpinned or of an unsupported kind is listed with its reason and
  refused at install;
* no install count exists anywhere in what the routes return.

Booby-trapped trees from ``tests/plugin_trees.py``: if anything ran, a ``PWNED`` file would appear.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import OWNER_DECISION_ROUTES, ROUTES
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from tests.plugin_trees import MARKER, full_plugin, text_plugin, write, zipped

URL = "http://127.0.0.1:65011"
SHA = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"

PLUGIN_ROUTES = {route_id for route_id in ROUTES if route_id.startswith("plugins.")}
OWNER_ONLY = {route_id for route_id in OWNER_DECISION_ROUTES if route_id.startswith("plugins.")}


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, full: bool = False) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _bridge(client: TestClient, app: Any, route: str, **kw: Any) -> Any:
    token = app.state.desktop_bridge.token or "no-token"
    return client.post(
        "/api/bridge/call", json={"route": route, **kw}, headers={"Authorization": f"Bearer {token}"}
    )


def _sources(tmp_path: Path) -> Path:
    folder = tmp_path / "sources"
    folder.mkdir(exist_ok=True)
    return folder


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _nothing_ran(tmp_path: Path) -> None:
    assert not list(tmp_path.rglob(f"{MARKER}*")), "something in a plugin was executed"


# --- the table ---------------------------------------------------------------------------------------


def test_the_reads_install_disable_and_update_are_operate_and_the_switch_is_nobodys_but_the_owners() -> None:
    assert {
        "plugins.list", "plugins.validate", "plugins.install", "plugins.disable",
        "plugins.update_check", "plugins.update", "plugins.suggestions",
        "plugins.marketplaces", "plugins.marketplace_plugins",
    } == PLUGIN_ROUTES
    assert all(ROUTES[r].tier == "operate" for r in PLUGIN_ROUTES)
    assert {
        "plugins.enable", "plugins.add_mcp", "plugins.remove",
        "plugins.marketplace_add", "plugins.marketplace_remove",
    } == OWNER_ONLY
    # No route of the table reaches the enable, remove or marketplace-writing paths by another name.
    reachable = {(ROUTES[r].method, ROUTES[r].path) for r in PLUGIN_ROUTES}
    assert ("POST", "/api/plugins/{name}/enable") not in reachable
    assert ("DELETE", "/api/plugins/{name}") not in reachable
    assert ("POST", "/api/plugins/marketplaces") not in reachable
    assert ("DELETE", "/api/plugins/marketplaces/{name}") not in reachable
    assert not any(path.startswith("/api/plugins/upload") for _, path in reachable)


# --- the owner's screen ------------------------------------------------------------------------------


def test_install_lands_pending_with_its_capabilities_and_enable_adds_only_the_named_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = full_plugin(_sources(tmp_path))
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        installed = client.post("/api/plugins/install", json={"source": str(source)})
        assert installed.status_code == 200, installed.text
        card = installed.json()
        assert card["status"] == "pending"
        assert card["author"] == "Someone Else"
        assert card["capabilities"]["process"] == 2  # two stdio MCP servers
        assert card["capabilities"]["exec"] >= 2  # the hook and bin/
        kinds = {(c["kind"], c["name"]): c for c in card["components"]}
        assert kinds[("skill", "greet")]["reaches_agent"] is True
        assert any(c["inert"] for c in card["components"] if c["kind"] == "hook")
        assert {s["name"] for s in card["servers"]} == {"files", "configured"}
        assert not any(s["added"] for s in card["servers"])

        on = client.post("/api/plugins/my-plugin/enable", json={"add_mcp": ["files"]})
        assert on.status_code == 200, on.text
        assert on.json()["status"] == "active"
        assert on.json()["mcp_added"] == ["plugin-my-plugin-files"]
        servers = {s["name"]: s["added"] for s in on.json()["servers"]}
        assert servers == {"files": True, "configured": False}
        mcp = json.loads((tmp_path / "home" / "mcp.json").read_text(encoding="utf-8"))
        assert "plugin-my-plugin-configured" not in json.dumps(mcp)

        off = client.post("/api/plugins/my-plugin/disable")
        assert off.json()["status"] == "inactive"
        assert off.json()["mcp_added"] == []

        listed = client.get("/api/plugins").json()
        assert listed["curated"] is False
        assert [p["name"] for p in listed["plugins"]] == ["my-plugin"]
        assert not _keys(listed) & {"installs", "install_count", "downloads", "stars", "users"}

        assert client.delete("/api/plugins/my-plugin").json() == {"removed": True}
        assert client.get("/api/plugins").json()["plugins"] == []
    _nothing_ran(tmp_path)
    get_settings.cache_clear()


def test_a_server_that_cannot_be_converted_is_refused_and_nothing_is_switched_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = full_plugin(_sources(tmp_path))
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.post("/api/plugins/install", json={"source": str(source)})
        refused = client.post("/api/plugins/my-plugin/enable", json={"add_mcp": ["configured"]})
        assert refused.status_code == 400
        assert "user_config" in refused.json()["detail"]
        assert client.get("/api/plugins").json()["plugins"][0]["status"] == "pending"
    get_settings.cache_clear()


def test_validate_reports_problems_and_copies_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = text_plugin(_sources(tmp_path))
    bad = _sources(tmp_path) / "empty-folder"
    bad.mkdir()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        ok = client.post("/api/plugins/validate", json={"path": str(good)}).json()
        assert ok["ok"] is True and ok["kind"] == "plugin" and ok["name"] == "notes"
        assert ok["capabilities"] == {"text": 2, "network": 0, "process": 0, "exec": 0}
        nope = client.post("/api/plugins/validate", json={"path": str(bad)}).json()
        assert nope["ok"] is False
        assert "not_a_plugin" in {p["code"] for p in nope["problems"]}
        relative = client.post("/api/plugins/validate", json={"path": "relative/folder"})
        assert relative.status_code == 400
    assert not (tmp_path / "home" / "plugins").exists()
    get_settings.cache_clear()


def test_an_update_shows_the_diff_first_and_applying_it_leaves_the_plugin_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = text_plugin(_sources(tmp_path))
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.post("/api/plugins/install", json={"source": str(source)})
        client.post("/api/plugins/notes/enable", json={})
        same = client.get("/api/plugins/notes/update").json()
        assert same == {"name": "notes", "changed": False, "status": "active", "applied": False, "diff": None}

        # A hook arrives upstream: governed.
        write(source, "hooks/hooks.json", json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": f"touch {MARKER}-later"}]}]}}))
        check = client.get("/api/plugins/notes/update").json()
        assert check["changed"] is True and check["status"] == "held" and check["applied"] is False
        assert "hooks/hooks.json" in check["diff"]["files_added"]
        assert check["diff"]["governed"] is True
        # The check changed nothing: still on.
        assert client.get("/api/plugins").json()["plugins"][0]["status"] == "active"

        applied = client.post("/api/plugins/notes/update", json={}).json()
        assert applied["applied"] is True and applied["status"] == "held"
        card = client.get("/api/plugins").json()["plugins"][0]
        assert card["status"] == "held"
        assert card["held_diff"]["governed"] is True
        assert any("hooks" in r for r in card["held_diff"]["reasons"])

        suggestions = client.get("/api/plugins/notes/suggestions").json()["suggestions"]
        assert suggestions and suggestions[0]["state"] == "inert"
        assert "touch" in suggestions[0]["text"]
    _nothing_ran(tmp_path)
    get_settings.cache_clear()


def _local_marketplace(tmp_path: Path) -> Path:
    root = _sources(tmp_path) / "market"
    text_plugin(root / "plugins", "notes")
    write(root, ".claude-plugin/marketplace.json", json.dumps({
        "name": "team", "owner": {"name": "Team"}, "description": "Our plugins",
        "plugins": [
            {"name": "notes", "source": "./plugins/notes", "category": "productivity",
             "description": "Only words."},
            {"name": "floating", "source": {"source": "github", "repo": "someone/floating"}},
            {"name": "from-npm", "source": {"source": "npm", "package": "@x/y"}},
            {"name": "pinned", "source": {"source": "github", "repo": "someone/pinned", "sha": SHA}},
        ],
    }))
    return root


def test_a_marketplace_lists_every_entry_and_only_the_pinned_ones_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = _local_marketplace(tmp_path)
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        added = client.post("/api/plugins/marketplaces", json={"source": str(market)})
        assert added.status_code == 200, added.text
        assert added.json()["name"] == "team" and added.json()["plugins"] == 4
        assert [m["name"] for m in client.get("/api/plugins/marketplaces").json()["marketplaces"]] == ["team"]

        entries = {
            e["name"]: e for e in client.get("/api/plugins/marketplaces/team/plugins").json()["plugins"]
        }
        assert entries["notes"]["installable"] is True
        assert entries["notes"]["capabilities"] == {"text": 2, "network": 0, "process": 0, "exec": 0}
        assert entries["floating"]["installable"] is False and "not pinned" in entries["floating"]["refusal"]
        assert entries["from-npm"]["installable"] is False and "npm" in entries["from-npm"]["refusal"]
        # A GitHub entry is not downloaded to answer a listing: unknown until installed.
        assert entries["pinned"]["capabilities"] is None

        refused = client.post("/api/plugins/install", json={"plugin": "floating", "marketplace": "team"})
        assert refused.status_code == 400 and "not installable" in refused.json()["detail"]
        ok = client.post("/api/plugins/install", json={"plugin": "notes", "marketplace": "team"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "pending" and ok.json()["marketplace"] == "team"
        after = client.get("/api/plugins/marketplaces/team/plugins").json()["plugins"]
        assert next(e for e in after if e["name"] == "notes")["installed"] == "pending"

        assert client.delete("/api/plugins/marketplaces/team").json() == {"removed": True}
        assert client.get("/api/plugins/marketplaces").json()["marketplaces"] == []
        # Forgetting the marketplace leaves what was installed from it.
        assert [p["name"] for p in client.get("/api/plugins").json()["plugins"]] == ["notes"]
    get_settings.cache_clear()


def _zip(folder: Path) -> bytes:
    return zipped(folder, top="notes-main")


def test_an_upload_lands_pending_a_second_one_asks_and_a_replacement_is_an_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = text_plugin(_sources(tmp_path))
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        first = client.post("/api/plugins/upload", files=[("files", ("notes.zip", _zip(source), "application/zip"))])
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "pending" and first.json()["source_kind"] == "local"

        again = client.post("/api/plugins/upload", files=[("files", ("notes.zip", _zip(source), "application/zip"))])
        assert again.status_code == 409

        write(source, "commands/brief.md", "---\ndescription: A brief\n---\nWrite a longer brief about $1.\n")
        replaced = client.post(
            "/api/plugins/upload?replace=true",
            files=[("files", ("notes.zip", _zip(source), "application/zip"))],
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["status"] == "pending"  # prose only: pending, not held
        assert replaced.json()["held_diff"]["files_changed"] == ["commands/brief.md"]

        cross = client.post(
            "/api/plugins/upload",
            files=[("files", ("x.zip", _zip(source), "application/zip"))],
            headers={"Origin": "https://evil.example"},
        )
        assert cross.status_code == 403
    get_settings.cache_clear()


def test_a_folder_upload_keeps_its_structure_and_a_hostile_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = text_plugin(_sources(tmp_path))
    files = sorted(p for p in source.rglob("*") if p.is_file())
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        ok = client.post(
            "/api/plugins/upload",
            files=[("files", (p.name, p.read_bytes(), "text/plain")) for p in files],
            data={"paths": [f"notes/{p.relative_to(source).as_posix()}" for p in files]},
        )
        assert ok.status_code == 200, ok.text
        assert {c["name"] for c in ok.json()["components"]} == {"summarise", "brief"}

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(".claude-plugin/plugin.json", json.dumps({"name": "evil"}))
            archive.writestr("../outside.md", "x")
        hostile = client.post("/api/plugins/upload", files=[("files", ("e.zip", buffer.getvalue(), "application/zip"))])
        assert hostile.status_code == 400
    assert not (tmp_path / "home" / "outside.md").exists()
    get_settings.cache_clear()


# --- the bridge --------------------------------------------------------------------------------------


@pytest.mark.parametrize("full", [False, True], ids=["operate", "full"])
def test_the_bridge_installs_pending_and_can_never_switch_a_plugin_on(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = full_plugin(_sources(tmp_path))
    market = _local_marketplace(tmp_path)
    app = _app(tmp_path, monkeypatch, full=full)
    with TestClient(app) as client:
        installed = _bridge(client, app, "plugins.install", body={"source": str(source)})
        assert installed.status_code == 200, installed.text
        assert installed.json()["status"] == 200
        assert installed.json()["data"]["status"] == "pending"

        for route, params, body in (
            ("plugins.enable", {"name": "my-plugin"}, {}),
            ("plugins.add_mcp", {"name": "my-plugin"}, {"add_mcp": ["files"]}),
            ("plugins.remove", {"name": "my-plugin"}, {}),
            ("plugins.marketplace_add", {}, {"source": str(market)}),
            ("plugins.marketplace_remove", {"name": "team"}, {}),
        ):
            refused = _bridge(client, app, route, params=params, body=body)
            assert refused.status_code == 403, route
            assert refused.json()["detail"] == OWNER_DECISION_ROUTES[route]

        # Nothing happened: still pending, no server added, no marketplace.
        card = client.get("/api/plugins").json()["plugins"][0]
        assert card["status"] == "pending" and card["mcp_added"] == []
        assert not (tmp_path / "home" / "mcp.json").exists()
        assert client.get("/api/plugins/marketplaces").json()["marketplaces"] == []

        listed = _bridge(client, app, "plugins.list").json()["data"]["plugins"]
        assert listed[0]["status"] == "pending"
        off = _bridge(client, app, "plugins.disable", params={"name": "my-plugin"})
        assert off.json()["data"]["status"] == "inactive"
        check = _bridge(client, app, "plugins.update_check", params={"name": "my-plugin"})
        assert check.json()["data"]["changed"] is False
    _nothing_ran(tmp_path)
    get_settings.cache_clear()


def test_through_the_bridge_a_hook_file_is_named_and_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = full_plugin(_sources(tmp_path))
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.post("/api/plugins/install", json={"source": str(source)})
        owner = client.get("/api/plugins/my-plugin/suggestions").json()["suggestions"]
        hook = next(s for s in owner if s["kind"] == "hook")
        assert MARKER in hook["text"]
        via = _bridge(client, app, "plugins.suggestions", params={"name": "my-plugin"}).json()["data"]
        bridged = next(s for s in via["suggestions"] if s["kind"] == "hook")
        assert "text" not in bridged and bridged["path"] == hook["path"]
    get_settings.cache_clear()


def test_the_bridge_cannot_name_the_apps_own_data_as_a_plugin_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    planted = text_plugin(home / "plugins-src", "planted")
    with TestClient(app) as client:
        for route, body in (
            ("plugins.install", {"source": str(planted)}),
            ("plugins.validate", {"path": str(planted)}),
            ("plugins.install", {"source": str(home)}),
        ):
            refused = _bridge(client, app, route, body=body)
            assert refused.status_code == 403, (route, refused.text)
        assert client.get("/api/plugins").json()["plugins"] == []
    get_settings.cache_clear()


def test_every_plugin_route_in_the_table_is_served_by_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    served = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}
    for route_id in PLUGIN_ROUTES:
        route = ROUTES[route_id]
        assert (route.method, route.path) in served, route_id
    for method, path in (
        ("POST", "/api/plugins/{name}/enable"),
        ("DELETE", "/api/plugins/{name}"),
        ("POST", "/api/plugins/marketplaces"),
        ("DELETE", "/api/plugins/marketplaces/{name}"),
    ):
        assert (method, path) in served
    # The upload sits on its own router (the route class that judges the body before it is parsed),
    # which `app.routes` does not list, exactly like the skill upload; the upload tests above reach it.
    get_settings.cache_clear()
