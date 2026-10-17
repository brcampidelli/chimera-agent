"""Clearing the cache, opening the app's folders, restarting and resetting are the OWNER's (C7).

Each acts on the owner's machine outside any conversation: a reset moves every conversation and
memory aside, and a folder window opens on their desktop. So none may be reached through the
desktop bridge — at either tier, by route id or by anything the caller writes — nor through the
guest's door, which knows four routes for one shared conversation.

And the words the backend may leave for the shell are exactly the words the shell accepts: the
Python list and `Request::parse` in `shell_request.rs` are kept equal here, by reading the Rust.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import OWNER_DECISION_ROUTES, ROUTES
from chimera.api.shell_requests import ACTIONS, REQUEST_ENV
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65004"
REPO = Path(__file__).resolve().parents[1]

OWNER_ONLY = {
    ("POST", "/api/storage/cache/clear"),
    ("GET", "/api/troubleshoot"),
    ("POST", "/api/troubleshoot/open-folder"),
    ("POST", "/api/troubleshoot/reset"),
    ("POST", "/api/troubleshoot/restart"),
}


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, full: bool) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
    monkeypatch.setenv(REQUEST_ENV, str(tmp_path / "app-data" / "shell-request.txt"))
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def test_the_owner_routes_exist_so_the_absence_below_means_something(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=False)
    served = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}
    assert served >= OWNER_ONLY


def test_no_troubleshooting_route_is_in_the_bridges_table() -> None:
    in_table = {(r.method, r.path) for r in ROUTES.values()}
    assert not in_table & OWNER_ONLY
    paths = [r.path for r in ROUTES.values()]
    assert not [p for p in paths if p.startswith(("/api/troubleshoot", "/api/storage"))]
    assert not [rid for rid in OWNER_DECISION_ROUTES if "troubleshoot" in rid or "storage" in rid]


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize(
    "route",
    [
        "storage.cache_clear",
        "troubleshoot.reset",
        "troubleshoot.open_folder",
        "/api/troubleshoot/reset",
        "app.reset",
    ],
)
def test_the_bridge_cannot_ask_for_a_troubleshooting_action(
    route: str, full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}

    response = client.post(
        "/api/bridge/call",
        json={"route": route, "body": {"word": "RESET", "folder": "logs", "confirm": True}},
        headers=headers,
    )

    assert response.status_code == 404, response.text
    assert not (tmp_path / "app-data" / "shell-request.txt").exists(), "the bridge reached the shell"


def test_the_guests_door_knows_none_of_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch, full=True)
    guest = getattr(app.state.guest_server, "_app", None)
    assert guest is not None, "the probe lost the guest app"
    guest_paths = {getattr(r, "path", "") for r in guest.routes}
    assert guest_paths, "the guest app has routes; the probe found none"
    assert not [p for p in guest_paths if "troubleshoot" in p or "storage" in p]


def test_the_backend_may_leave_exactly_the_words_the_shell_accepts() -> None:
    source = (REPO / "apps/desktop/src-tauri/src/shell_request.rs").read_text(encoding="utf-8")
    parse = source.split("pub fn parse", 1)[1].split("_ => None", 1)[0]
    rust_words = set(re.findall(r'"(\w+)" => Some\(Request::', parse))
    assert rust_words, "the probe found no word in Request::parse"
    assert rust_words == set(ACTIONS)
