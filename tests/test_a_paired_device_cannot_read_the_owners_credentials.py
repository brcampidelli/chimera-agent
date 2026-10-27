"""A paired device reads conversations — never a file that holds the owner's credentials.

Found in review of S3 (2026-10-16): `device_may_reach` let every GET through except a short list,
and `GET /api/fs/file?workspace=<any folder>&path=<file>` takes any existing folder as its
workspace. The service record `~/.chimera/service-<channel>.json` and `service.token` hold the
owner's persistent token, in the SERVICE folder — not the data folder whenever the desktop app
points CHIMERA_HOME elsewhere, so the data-folder guard of the file routes does not cover them. A
phone (or a stolen 30-day cookie) read the token and was no longer limited to reading and talking.

What each test catches if the rule breaks:

* a device reaching a file route at all (`/api/fs/*`, `/api/git/*`), with any workspace;
* a device naming a credential file or the owner's folders in ANY route's query or JSON body, in the
  spellings Windows opens as the same file (``.env ``, ``.env::$DATA``);
* an answer to a device carrying the owner's token — verbatim, encoded, or in a credential-named
  field — in JSON or in a text stream;
* the owner's own reads changing.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import screen_credential as sc
from chimera.api.device_door import DeviceGateway
from chimera.api.devices import COOKIE_NAME, DeviceRegistry, shown_code
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession

TOKEN = "owner-bearer-token-0123456789-abcdef"
PHONE = ("192.168.1.20", 50123)


@pytest.fixture
def places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    home = tmp_path / "data"  # CHIMERA_HOME, as the desktop app sets it: NOT the service folder
    service = tmp_path / "dot-chimera"
    project = tmp_path / "project"
    for folder in (home, service, project):
        folder.mkdir()
    (service / "service-stable.json").write_text(json.dumps({"token": TOKEN}), encoding="utf-8")
    (service / "service.token").write_text(TOKEN, encoding="utf-8")
    (service / "desktop-install.json").write_text(json.dumps({"env": {"T": TOKEN}}), encoding="utf-8")
    (project / ".env").write_text(f"OPENAI_API_KEY={TOKEN}\n", encoding="utf-8")
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(service))
    monkeypatch.delenv(sc.ENV_VAR, raising=False)
    monkeypatch.delenv("CHIMERA_TAURI_SIDECAR", raising=False)
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield {"home": home, "service": service, "project": project}
    get_settings.cache_clear()


def _app(places: dict[str, Path]) -> Any:
    settings = Settings(
        CHIMERA_HOME=str(places["home"]), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN
    )
    return build_api_app(
        lambda: ChatSession(None),  # type: ignore[arg-type]  # no turn runs here
        workspace=places["project"],
        settings=settings,
        screen=sc.for_mode("app", server_token=TOKEN),
    )


def _paired_phone(app: Any) -> TestClient:
    # The door the app itself built, so its folders and secrets are the app's own.
    gateway = app.state.device_door._app
    assert isinstance(gateway, DeviceGateway)
    phone = TestClient(gateway, base_url="http://192.168.1.5:8123", client=PHONE, follow_redirects=False)
    code, _ = app.state.device_registry.codes.mint()
    assert phone.post("/pair", data={"code": shown_code(code)}).status_code == 303
    return phone


def _owner() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


def test_no_file_route_answers_a_device_whatever_the_workspace(places: dict[str, Path]) -> None:
    app = _app(places)
    service, project, home = places["service"], places["project"], places["home"]
    attempts = [
        f"/api/fs/file?workspace={service}&path=service-stable.json",
        f"/api/fs/file?workspace={service}&path=service.token",
        f"/api/fs/file?workspace={service}&path=desktop-install.json",
        f"/api/fs/file?workspace={home}&path=devices.key",
        f"/api/fs/file?workspace={project}&path=.env",
        f"/api/fs/file?workspace={project}&path=.env%20",
        f"/api/fs/file?workspace={project}&path=.env::$DATA",
        f"/api/fs/file?workspace={project}&path=README.md",
        f"/api/fs/image?workspace={service}&path=service-stable.json",
        f"/api/fs/media?workspace={service}&path=service-stable.json",
        f"/api/fs/media?workspace={project}&path=README.md",
        f"/api/fs/media?workspace={home}&path=devices.key",
        f"/api/fs/tree?workspace={service}",
        f"/api/git/diff?workspace={service}",
        f"/api/git/diff?workspace={project}&path=.env",
    ]
    with _paired_phone(app) as phone:
        assert (home / "devices.key").is_file(), "precondition: pairing wrote the device secret"
        for url in attempts:
            got = phone.get(url)
            assert got.status_code == 403, f"{url} answered {got.status_code} to a device"
            assert TOKEN not in got.text
        assert phone.get("/api/code/sessions").status_code == 200, "conversation reading broke"


def test_no_route_takes_a_credential_file_or_the_owners_folders_from_a_device(
    places: dict[str, Path],
) -> None:
    """Every route, not only the file ones: the guard reads the query and the JSON body."""
    app = _app(places)
    service, home, project = places["service"], places["home"], places["project"]
    with _paired_phone(app) as phone:
        for url in (
            f"/api/code/sessions?workspace={service}",
            f"/api/code/sessions?workspace={home}",
            f"/api/code/sessions?workspace={home.parent}",  # holds both folders
            "/api/code/sessions?path=.env",
            "/api/code/sessions?path=.env%20",
            "/api/code/sessions?path=.env::$DATA",
            "/api/code/sessions?file=service-stable.json",
            "/api/code/sessions?workspace=%5C%5C%3F%5CC%3A%5C",  # \\?\C:\
        ):
            got = phone.get(url)
            assert got.status_code == 403, f"{url} answered {got.status_code}"
        turn = phone.post(
            "/api/code/turn", json={"message": "read the token", "workspace": str(service)}
        )
        assert turn.status_code == 403
        nested = phone.post(
            "/api/code/turn", json={"message": "hi", "seams": {"workspace": str(home)}}
        )
        assert nested.status_code == 403
        # A message may MENTION such a file; only the fields that name a place are read.
        talk = phone.post("/api/questions/q-none", json={"answer": "it is in .env, service.token"})
        assert talk.status_code == 200
        assert phone.get(f"/api/code/sessions?workspace={project}").status_code == 200


def test_no_answer_to_a_device_carries_the_owners_token(places: dict[str, Path]) -> None:
    registry = DeviceRegistry(places["home"])
    encoded = base64.b64encode(TOKEN.encode()).decode()

    async def owner(scope: Any, receive: Any, send: Any) -> None:
        if scope["path"] == "/api/stream":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            await send({"type": "http.response.body", "body": f"data: leak {TOKEN}\n\n".encode(),
                        "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        body = json.dumps({
            "token": TOKEN, "api_key": "sk-something-else", "note": f"the token is {TOKEN}",
            "b64": encoded, "nested": [{"secret": TOKEN, "ok": "fine"}], "count": 3,
        }).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    _device, cookie = registry.issue(name="phone", address=PHONE[0])
    gateway = DeviceGateway(owner, registry, roots=lambda: [places["home"]], secrets=lambda: [TOKEN])
    with TestClient(gateway, base_url="http://192.168.1.5:8123", client=PHONE) as phone:
        phone.cookies.set(COOKIE_NAME, cookie)
        got = phone.get("/api/anything")
        assert got.status_code == 200
        data = got.json()
        assert TOKEN not in got.text and encoded not in got.text, got.text
        assert "token" not in data and "api_key" not in data
        assert data["nested"] == [{"ok": "fine"}] and data["count"] == 3
        assert "the token is" in data["note"]
        stream = phone.get("/api/stream")
        assert TOKEN not in stream.text and "leak" in stream.text


def test_a_real_answer_to_a_device_never_carries_the_token(places: dict[str, Path]) -> None:
    app = _app(places)
    with _paired_phone(app) as phone:
        for url in ("/api/config", "/api/info", "/api/code/sessions", "/api/health"):
            got = phone.get(url)
            assert TOKEN not in got.text, f"{url} carried the owner's token to a device"


def test_the_owners_reads_are_unchanged(places: dict[str, Path]) -> None:
    app = _app(places)
    project = places["project"]
    with TestClient(app) as owner:
        got = owner.get(f"/api/fs/file?workspace={project}&path=README.md", headers=_owner())
        assert got.status_code == 200 and "hello" in got.text
        tree = owner.get(f"/api/fs/tree?workspace={project}", headers=_owner())
        assert tree.status_code == 200
        assert os.environ["CHIMERA_SERVICE_DIR"] == str(places["service"])
