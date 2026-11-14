"""Who may open a terminal, and where.

The terminal is a shell, so the door is narrower than the app's other routes. What each test would
catch if the rule broke:

* the bearer token alone (a file the agent's shell can read) opening one;
* a browser tab paired with a code, the Claude bridge or a paired device opening one;
* a folder with commands off getting a terminal anyway;
* a container sandbox being stepped around;
* the route being reachable from the bridge's table.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import pty_terminal as pt
from chimera.api import screen_credential as sc
from chimera.api.bridge_routes import ROUTES, VIA_BRIDGE_SCOPE_KEY
from chimera.api.browser_screen import BROWSER_SCOPE_KEY, DESKTOP_SCREEN_SCOPE_KEY
from chimera.api.devices import DEVICE_SCOPE_KEY
from chimera.api.terminal_api import (
    COMMANDS_OFF_DETAIL,
    SANDBOX_DETAIL,
    owner_refusal,
)
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

TOKEN = "owner-bearer-token-for-terminal-tests-0123456789"
SCREEN = "desktop-screen-credential-for-terminal-tests-0123456789"
BEARER = {"authorization": f"Bearer {TOKEN}"}
_OK, _WHY = pt.available()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    pt.manager().close_all()
    get_settings.cache_clear()


def _app(tmp_path: Path, **extra: str) -> Any:
    get_settings.cache_clear()
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(
        CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN, **extra
    )
    return build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)),
        workspace=ws,
        settings=settings,
        screen=sc.ScreenCredential(value=SCREEN),
    )


def _desktop(app: Any) -> TestClient:
    return TestClient(app, headers={**BEARER, sc.HEADER_NAME: SCREEN})


def _req(**scope: Any) -> Any:
    return SimpleNamespace(scope=scope)


def test_only_the_desktop_screen_passes_the_owner_check() -> None:
    assert owner_refusal(_req(**{DESKTOP_SCREEN_SCOPE_KEY: True})) == ""
    assert "bridge" in owner_refusal(_req(**{VIA_BRIDGE_SCOPE_KEY: True, DESKTOP_SCREEN_SCOPE_KEY: True}))
    assert "device" in owner_refusal(_req(**{DEVICE_SCOPE_KEY: "d", DESKTOP_SCREEN_SCOPE_KEY: True}))
    assert "browser" in owner_refusal(_req(**{BROWSER_SCOPE_KEY: "s", DESKTOP_SCREEN_SCOPE_KEY: True}))
    assert owner_refusal(_req()) != ""


def test_the_bridge_table_has_no_terminal_route() -> None:
    assert not [r for r in ROUTES.values() if r.path.startswith("/api/terminal")]


def test_the_bearer_token_alone_does_not_open_a_terminal(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    client = TestClient(app, headers=BEARER)
    assert client.post("/api/terminal", json={}).status_code == 403
    assert client.get("/api/terminal/status").status_code == 403
    assert client.post("/api/terminal/abc/input", json={"data": "ls\n"}).status_code == 403
    assert pt.manager().count() == 0


def test_a_request_with_no_credentials_is_refused(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    assert TestClient(app).post("/api/terminal", json={}).status_code == 401


def test_a_page_offered_screen_is_not_the_desktop(tmp_path: Path) -> None:
    get_settings.cache_clear()
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(
        CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json",
        CHIMERA_SERVER_TOKEN=TOKEN, CHIMERA_REACH="workspace_shell",
    )
    app = build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)), workspace=ws, settings=settings,
        screen=sc.ScreenCredential(value=SCREEN, in_page=True),
    )
    assert _desktop(app).post("/api/terminal", json={}).status_code == 403


def test_commands_off_means_no_terminal_and_says_why(tmp_path: Path) -> None:
    app = _app(tmp_path)  # no grant for the folder, no CHIMERA_REACH
    response = _desktop(app).post("/api/terminal", json={})
    assert response.status_code == 403
    assert response.json()["detail"] == COMMANDS_OFF_DETAIL
    assert pt.manager().count() == 0


def test_a_denied_shell_tool_means_no_terminal(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell", CHIMERA_TOOL_DENYLIST="run_shell")
    response = _desktop(app).post("/api/terminal", json={})
    assert response.status_code == 403
    assert response.json()["detail"] == COMMANDS_OFF_DETAIL


def test_a_container_sandbox_is_not_stepped_around(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell", CHIMERA_SANDBOX="docker")
    response = _desktop(app).post("/api/terminal", json={})
    assert response.status_code == 403
    assert response.json()["detail"] == SANDBOX_DETAIL


def test_a_missing_folder_is_a_400(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    response = _desktop(app).post("/api/terminal", json={"workspace": str(tmp_path / "nope")})
    assert response.status_code == 400


def test_status_reports_availability(tmp_path: Path) -> None:
    app = _app(tmp_path)
    body = _desktop(app).get("/api/terminal/status").json()
    assert body["available"] is _OK
    assert body["max"] == pt.MAX_SESSIONS
    if not _OK:
        assert body["reason"] == _WHY


@pytest.mark.skipif(not _OK, reason=_WHY or "no pty")
def test_the_owner_opens_types_and_closes(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    client = _desktop(app)
    opened = client.post("/api/terminal", json={"cols": 100, "rows": 30})
    assert opened.status_code == 200, opened.text
    tid = opened.json()["id"]
    assert opened.json()["cwd"] == str((tmp_path / "ws").resolve())
    assert client.post(f"/api/terminal/{tid}/input", json={"data": "echo hi\n"}).status_code == 200
    assert client.post(f"/api/terminal/{tid}/resize", json={"cols": 90, "rows": 20}).status_code == 200
    assert client.post(f"/api/terminal/{tid}/resize", json={"cols": 1, "rows": 20}).status_code == 422
    assert client.delete(f"/api/terminal/{tid}").status_code == 200
    import time

    for _ in range(100):
        if pt.manager().get(tid) is None:
            break
        time.sleep(0.1)
    assert pt.manager().get(tid) is None
    assert client.post(f"/api/terminal/{tid}/input", json={"data": "x"}).status_code == 404


@pytest.mark.skipif(not _OK, reason=_WHY or "no pty")
def test_the_ninth_tab_is_refused(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    client = _desktop(app)
    codes = [client.post("/api/terminal", json={}).status_code for _ in range(pt.MAX_SESSIONS + 1)]
    assert codes[: pt.MAX_SESSIONS] == [200] * pt.MAX_SESSIONS
    assert codes[-1] == 429


# ---- the stream, the input, and the other doors --------------------------------------------------


def _paired_browser(app: Any) -> TestClient:
    """A browser tab the owner paired with a code: the owner's screen for reading and talking."""
    minted = _desktop(app).post("/api/security/browser-screen/code")
    assert minted.status_code == 200, minted.text
    browser = TestClient(app, headers={**BEARER, "sec-fetch-site": "same-origin"})
    exchanged = browser.post("/api/security/browser-screen/exchange", json={"code": minted.json()["code"]})
    assert exchanged.status_code == 200, exchanged.text
    return browser


@pytest.mark.skipif(not _OK, reason=_WHY or "no pty")
def test_a_paired_browser_tab_cannot_open_read_or_type_into_a_terminal(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    owner = _desktop(app)
    tid = owner.post("/api/terminal", json={}).json()["id"]
    browser = _paired_browser(app)
    assert browser.post("/api/terminal", json={}).status_code == 403
    assert browser.get(f"/api/terminal/{tid}/stream").status_code == 403
    assert browser.post(f"/api/terminal/{tid}/input", json={"data": "id\n"}).status_code == 403
    assert browser.post(f"/api/terminal/{tid}/resize", json={"cols": 80, "rows": 24}).status_code == 403
    assert browser.delete(f"/api/terminal/{tid}").status_code == 403
    assert pt.manager().get(tid) is not None, "a refused delete must not have closed the owner's shell"


def test_a_paired_device_gets_nothing_of_the_terminal(tmp_path: Path) -> None:
    from chimera.api.device_door import DeviceGateway
    from chimera.api.devices import COOKIE_NAME, device_may_reach, shown_code

    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    # The door's own table, which a stream (a GET) would otherwise pass.
    for method, path in (("GET", "/api/terminal/x/stream"), ("POST", "/api/terminal"), ("GET", "/api/terminal/status")):
        assert device_may_reach(method, path), (method, path)
    gateway = DeviceGateway(app, app.state.device_registry, watch_seconds=0.05)
    phone = TestClient(
        gateway, base_url="http://192.168.1.5:8123", client=("192.168.1.20", 50123), follow_redirects=False
    )
    code, _ = app.state.device_registry.codes.mint()
    assert phone.post("/pair", data={"code": shown_code(code), "name": "phone"}).status_code == 303
    assert phone.cookies.get(COOKIE_NAME)
    for response in (
        phone.get("/api/terminal/status"),
        phone.get("/api/terminal/abc/stream"),
        phone.post("/api/terminal", json={}),
        phone.post("/api/terminal/abc/input", json={"data": "x"}),
    ):
        assert response.status_code in (403, 404), response.text
    assert pt.manager().count() == 0


def test_the_bridge_cannot_reach_a_terminal_at_any_tier(tmp_path: Path) -> None:
    from chimera.api.bridge_routes import FULL_CONTROL_ROUTES, OWNER_DECISION_ROUTES

    for table in (ROUTES, FULL_CONTROL_ROUTES):
        assert not [r for r in table.values() if r.path.startswith("/api/terminal")]
    assert not [k for k in OWNER_DECISION_ROUTES if "terminal" in k]
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    client = TestClient(app)
    for route in ("terminal.open", "settings.terminal", "terminal"):
        response = client.post("/api/bridge/call", json={"route": route, "body": {}}, headers=headers)
        assert response.status_code >= 400, (route, response.text)
    assert pt.manager().count() == 0


@pytest.mark.skipif(not _OK, reason=_WHY or "no pty")
def test_input_is_refused_once_the_folders_commands_are_turned_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import terminal_api

    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    client = _desktop(app)
    tid = client.post("/api/terminal", json={}).json()["id"]
    assert client.post(f"/api/terminal/{tid}/input", json={"data": "echo ok\n"}).status_code == 200
    # The owner switches commands off on the Code screen while the shell is open.
    monkeypatch.setattr(terminal_api, "folder_refusal", lambda *_a, **_k: terminal_api.COMMANDS_OFF_DETAIL)
    pt.manager().get(tid).checked_at = 0.0  # type: ignore[union-attr]  # the one-second cache has run out
    refused = client.post(f"/api/terminal/{tid}/input", json={"data": "rm -rf build\n"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == terminal_api.COMMANDS_OFF_DETAIL


@pytest.mark.skipif(not _OK, reason=_WHY or "no pty")
def test_a_stream_that_never_starts_leaves_the_session_for_its_timer(tmp_path: Path) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    client = _desktop(app)
    tid = client.post("/api/terminal", json={}).json()["id"]
    session = pt.manager().get(tid)
    assert session is not None and not session.attached, "creating a shell must not count as attaching"


@pytest.mark.skipif(not _OK, reason=_WHY or "no pty")
def test_the_stream_that_loses_the_claim_gets_an_error_event_and_the_winner_keeps_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, CHIMERA_REACH="workspace_shell")
    client = _desktop(app)
    tid = client.post("/api/terminal", json={}).json()["id"]
    session = pt.manager().get(tid)
    assert session is not None
    # The race: the route's own 409 check passed (nobody attached yet), and by the time the generator
    # claims, another stream has. Made deterministic by having the claim answer "lost".
    monkeypatch.setattr(session, "claim", lambda: False)
    with client.stream("GET", f"/api/terminal/{tid}/stream") as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: error" in body and "already shown" in body
    assert pt.manager().get(tid) is not None and not session.closed, "the loser must not close the winner's shell"
