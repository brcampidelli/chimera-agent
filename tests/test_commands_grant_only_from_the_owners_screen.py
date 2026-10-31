"""Only the owner's screen can grant a folder's commands: the answer "Liberar" through the real route.

The question is parked the way the turn parks it (``ask_user_durably``, tool ``shell_permission``), and
answered with a real ``POST /api/questions/{id}``. Only the screen credential may answer "Liberar"; a
bearer token, a bridge call with full control and a paired phone are refused, and the question keeps
waiting. "Cancelar" grants nothing, so any caller the guard admits may give it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.api import build_api_app  # noqa: E402
from chimera.api import screen_credential as sc  # noqa: E402
from chimera.api.code_api import COMMANDS_OFF_OPTIONS, COMMANDS_OFF_QUESTION  # noqa: E402
from chimera.api.desktop_bridge import asgi_call  # noqa: E402
from chimera.api.device_door import DeviceGateway  # noqa: E402
from chimera.api.devices import COOKIE_NAME, shown_code  # noqa: E402
from chimera.config import Settings, get_settings  # noqa: E402
from chimera.governance.pending import ask_user_durably  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402

TOKEN = "owner-bearer-for-the-shell-grant-tests"
TIMEOUT = 10.0
PHONE = ("192.168.1.20", 50123)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    folder = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(folder))
    monkeypatch.delenv(sc.ENV_VAR, raising=False)
    monkeypatch.delenv("CHIMERA_TAURI_SIDECAR", raising=False)
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    get_settings.cache_clear()
    yield folder
    get_settings.cache_clear()


@pytest.fixture
def app(home: Path, tmp_path: Path) -> Any:
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN)
    return build_api_app(
        lambda: ChatSession(None),  # type: ignore[arg-type]  # no turn runs here
        workspace=tmp_path,
        settings=settings,
        screen=sc.for_mode("app", server_token=TOKEN),
    )


def _bearer() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


def _screen(app: Any) -> dict[str, str]:
    from chimera.api.screen_credential import HEADER_NAME

    return {**_bearer(), HEADER_NAME: str(app.state.screen_credential.value)}


def _park(home: Path) -> tuple[threading.Thread, dict[str, Any]]:
    """Park the question as the turn does, and wait until it is announced."""
    seen: dict[str, Any] = {}
    result: dict[str, Any] = {"meta": {}}

    def announce(request_id: str, data: dict[str, Any]) -> None:
        seen["id"] = request_id

    def park() -> None:
        result["answer"] = ask_user_durably(
            home, COMMANDS_OFF_QUESTION, list(COMMANDS_OFF_OPTIONS), False,
            announce=announce, wait_seconds=8.0, poll_seconds=0.02, run_id="turn-shell",
            tool="shell_permission", answer_meta=result["meta"],
        )

    thread = threading.Thread(target=park, daemon=True)
    thread.start()
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and "id" not in seen:
        time.sleep(0.01)
    assert "id" in seen, "the question was never announced"
    return thread, {"id": seen["id"], "result": result}


def _finish_from_screen(app: Any, thread: threading.Thread, request_id: str) -> None:
    """Answer from the screen so a parked question's thread ends, then wait for it."""
    TestClient(app).post(f"/api/questions/{request_id}", json={"answer": "Cancelar"}, headers=_screen(app))
    thread.join(TIMEOUT)


def _phone_client(app: Any) -> TestClient:
    """A paired phone on the LAN, as the device door reaches the app."""
    gateway = DeviceGateway(app, app.state.device_registry, watch_seconds=0.05)
    phone = TestClient(gateway, base_url="http://192.168.1.5:8123", client=PHONE, follow_redirects=False)
    code, _ = app.state.device_registry.codes.mint()
    got = phone.post("/pair", data={"code": shown_code(code), "name": "Bruno's phone"})
    assert got.status_code == 303, got.text
    assert phone.cookies.get(COOKIE_NAME), "no device cookie was set"
    return phone


def test_the_owners_screen_grants_commands_through_the_route(app: Any, home: Path) -> None:
    thread, parked = _park(home)
    response = TestClient(app).post(
        f"/api/questions/{parked['id']}", json={"answer": "Liberar"}, headers=_screen(app)
    )
    thread.join(TIMEOUT)

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert parked["result"]["answer"] == "Liberar"
    assert parked["result"]["meta"].get("via") == "screen", "the grant is recorded as the screen's"


def test_a_bearer_token_cannot_grant_commands(app: Any, home: Path) -> None:
    thread, parked = _park(home)
    try:
        refused = TestClient(app).post(
            f"/api/questions/{parked['id']}", json={"answer": "Liberar"}, headers=_bearer()
        )
        assert refused.status_code == 403, refused.text
        assert thread.is_alive(), "the question is still waiting for the owner's screen"
    finally:
        _finish_from_screen(app, thread, parked["id"])


def test_a_paired_phone_cannot_grant_commands(app: Any, home: Path) -> None:
    thread, parked = _park(home)
    try:
        phone = _phone_client(app)
        refused = phone.post(f"/api/questions/{parked['id']}", json={"answer": "Liberar"})
        assert refused.status_code == 403, refused.text
        assert thread.is_alive(), "a paired phone recorded nothing"
    finally:
        _finish_from_screen(app, thread, parked["id"])


def test_a_bridge_call_with_full_control_cannot_grant_commands(app: Any, home: Path) -> None:
    thread, parked = _park(home)
    try:
        status, _body = asyncio.run(asgi_call(
            app, "POST", f"/api/questions/{parked['id']}", body={"answer": "Liberar"},
            headers=_bearer(), full_control=True,
        ))
        assert status == 403, status
        assert thread.is_alive(), "a bridge with full control recorded nothing"
    finally:
        _finish_from_screen(app, thread, parked["id"])


def test_cancelar_from_a_token_is_accepted_and_grants_nothing(app: Any, home: Path, tmp_path: Path) -> None:
    thread, parked = _park(home)
    response = TestClient(app).post(
        f"/api/questions/{parked['id']}", json={"answer": "Cancelar"}, headers=_bearer()
    )
    thread.join(TIMEOUT)

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert parked["result"]["answer"] == "Cancelar"
    from chimera.core.code_projects import CodeProjectRegistry

    assert not CodeProjectRegistry(home / "code_projects.json").shell_granted_for(tmp_path)
