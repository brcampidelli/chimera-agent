"""Who may start a dev server from the Code screen, and where.

Starting a dev server is running a command, so the door is the terminal's: only the desktop app's own
screen, never the bearer token alone, a paired browser tab, a device, the bridge or a guest; and never
in a folder whose commands are off.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import screen_credential as sc
from chimera.api.bridge_routes import ROUTES
from chimera.api.terminal_api import COMMANDS_OFF_DETAIL
from chimera.config import Settings, get_settings
from chimera.core.dev_servers import manager
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

TOKEN = "owner-bearer-token-for-preview-tests-0123456789"
SCREEN = "desktop-screen-credential-for-preview-tests-0123456789"
BEARER = {"authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    manager().close_all()
    get_settings.cache_clear()


# Built by concatenation so the literal never appears in the file (secret scanners read text).
FAKE_KEY = "sk_" + "live_" + "x" * 24


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _app(tmp_path: Path, **extra: str) -> Any:
    get_settings.cache_clear()
    ws = tmp_path / "ws"
    (ws / ".chimera").mkdir(parents=True, exist_ok=True)
    (ws / ".chimera" / "launch.json").write_text(json.dumps({"configurations": [{
        "name": "web", "runtimeExecutable": "python",
        "runtimeArgs": ["-m", "http.server", "${PORT}", "--bind", "127.0.0.1"], "port": _port(),
        "env": {"STRIPE_SECRET_KEY": FAKE_KEY},
    }]}), encoding="utf-8")
    settings = Settings(
        CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN,
        CHIMERA_SANDBOX=extra.pop("CHIMERA_SANDBOX", "local"), **extra
    )
    return build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)), workspace=ws, settings=settings,
        screen=sc.ScreenCredential(value=SCREEN),
    )


def _desktop(app: Any) -> TestClient:
    return TestClient(app, headers={**BEARER, sc.HEADER_NAME: SCREEN})


def test_the_bridge_table_has_no_preview_route() -> None:
    assert not [r for r in ROUTES.values() if r.path.startswith("/api/preview")]


def test_the_bearer_token_alone_starts_nothing(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, CHIMERA_REACH="workspace_shell"), headers=BEARER)
    assert client.get("/api/preview/servers").status_code == 403
    assert client.post("/api/preview/servers/start", json={"name": "web"}).status_code == 403
    assert client.post("/api/preview/servers/stop", json={"name": "web"}).status_code == 403
    assert client.get("/api/preview/servers/log", params={"name": "web"}).status_code == 403
    assert manager().running_count() == 0


def test_no_credentials_is_a_401(tmp_path: Path) -> None:
    assert TestClient(_app(tmp_path)).get("/api/preview/servers").status_code == 401


def test_the_list_reads_the_recipe_and_warns_about_a_secret_without_repeating_it(tmp_path: Path) -> None:
    body = _desktop(_app(tmp_path, CHIMERA_REACH="workspace_shell")).get("/api/preview/servers").json()
    assert body["found"] and body["source"] == ".chimera/launch.json"
    assert body["servers"][0]["name"] == "web" and body["servers"][0]["state"] == "stopped"
    assert body["can_start"] is True and body["auto_verify"] is True and body["auto_verify_setting"] is True
    assert any("STRIPE_SECRET_KEY" in w for w in body["warnings"])
    assert "sk_live" not in json.dumps(body["warnings"])  # the warning never repeats it; the command shows env by design


def test_commands_off_means_no_server_and_says_why(tmp_path: Path) -> None:
    client = _desktop(_app(tmp_path))  # no grant for the folder
    listing = client.get("/api/preview/servers").json()
    assert listing["can_start"] is False and listing["cannot_start_reason"] == COMMANDS_OFF_DETAIL
    response = client.post("/api/preview/servers/start", json={"name": "web"})
    assert response.status_code == 403 and response.json()["detail"] == COMMANDS_OFF_DETAIL
    assert manager().running_count() == 0


def test_a_denied_shell_tool_means_no_server(tmp_path: Path) -> None:
    client = _desktop(_app(tmp_path, CHIMERA_REACH="workspace_shell", CHIMERA_TOOL_DENYLIST="run_shell"))
    assert client.post("/api/preview/servers/start", json={"name": "web"}).status_code == 403


def test_an_unknown_name_is_a_404_and_a_name_is_all_the_route_takes(tmp_path: Path) -> None:
    client = _desktop(_app(tmp_path, CHIMERA_REACH="workspace_shell"))
    assert client.post("/api/preview/servers/start", json={"name": "nope"}).status_code == 404
    # A command in the body is not a field: the model is strict about extra input being ignored, and
    # the name is the only thing that selects what runs.
    response = client.post("/api/preview/servers/start", json={"name": "nope", "command": "calc"})
    assert response.status_code == 404
    assert manager().running_count() == 0


def test_the_owner_starts_reads_the_log_and_stops(tmp_path: Path) -> None:
    import time

    client = _desktop(_app(tmp_path, CHIMERA_REACH="workspace_shell"))
    started = client.post("/api/preview/servers/start", json={"name": "web"})
    assert started.status_code == 200, started.text
    for _ in range(150):
        state = client.get("/api/preview/servers").json()["servers"][0]
        if state["state"] == "running":
            break
        time.sleep(0.2)
    assert state["state"] == "running" and state["url"].startswith("http://localhost:")
    log = client.get("/api/preview/servers/log", params={"name": "web"}).json()["lines"]
    assert log and log[0].startswith("$ ")
    assert client.post("/api/preview/servers/stop", json={"name": "web"}).json()["message"] == "stopped"
    assert client.get("/api/preview/servers").json()["servers"][0]["state"] in ("exited", "failed")


def test_a_hard_block_stays_blocked_even_when_the_owner_clicks(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    ws = tmp_path / "ws"
    (ws / ".chimera" / "launch.json").write_text(json.dumps({"configurations": [
        {"name": "bad", "runtimeExecutable": "rm", "runtimeArgs": ["-rf", "/"], "port": _port()},
    ]}), encoding="utf-8")
    response = _desktop(app).post("/api/preview/servers/start", json={"name": "bad"})
    assert response.status_code == 403 and "safety rules" in response.json()["detail"]
    assert manager().running_count() == 0


def test_the_panel_shows_the_whole_command_the_folder_and_what_changed_since_the_turn(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    ws = tmp_path / "ws"
    manager().watch_turn(ws, lambda: ["package.json"])
    try:
        body = _desktop(app).get("/api/preview/servers").json()
    finally:
        manager().watch_turn(ws, lambda: [])
    row = body["servers"][0]
    assert "STRIPE_SECRET_KEY" in row["command"] and "PORT" in row["command"] and "http.server" in row["command"]
    assert row["cwd"] == "."
    assert body["config_changed"] == ["package.json"]
