"""The desktop bridge exists only while the owner has it switched on, and only for its token.

`chimera mcp desktop` reaches the running app through a discovery file holding the app's URL and a
random token (`chimera/api/desktop_bridge.py`). What these tests hold:

* the file appears when the switch is on and the app knows its port, and not otherwise;
* on POSIX nobody but the user can read it;
* turning the switch off — through the same `PATCH /api/config` the Settings screen sends — deletes
  it at once and the old token stops working; turning it on again mints a NEW token;
* the bridge answers 403 while off and 401 to a wrong token, and the SPA's own routes do not care;
* the app only ever deletes its OWN file, and clears one a crashed app left on its port.

Every path is under `tmp_path`: `CHIMERA_BRIDGE_DIR` points the file there (the conftest does it
for every test, so nothing here can touch a real app's file), and `.env` is written in a chdir'd
temporary directory.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.desktop_bridge import DesktopBridge, bridge_path, read_discovery, write_discovery
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65001"


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, on: bool, full: bool = False) -> Any:
    """A real app over the live settings (so a PATCH is seen), its bridge told its port."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    # Owned before anything writes them: `patch_config` sets os.environ for real.
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true" if on else "false")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_the_file_lives_where_the_test_said_not_in_the_real_home(tmp_path: Path) -> None:
    """The guard every other test here relies on: the conftest's override is in force."""
    assert bridge_path() != Path.home() / ".chimera" / "desktop-bridge.json"
    assert bridge_path().parent == Path(os.environ["CHIMERA_BRIDGE_DIR"])
    assert bridge_path().name == "desktop-bridge.json"


def test_off_by_default_no_file_and_the_bridge_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Settings.model_fields["desktop_bridge"].default is False
    assert Settings.model_fields["desktop_bridge_full"].default is False
    app = _app(tmp_path, monkeypatch, on=False)

    assert not bridge_path().exists()
    with TestClient(app) as client:
        response = client.get("/api/bridge/status", headers=_bearer("anything"))
    assert response.status_code == 403


def test_on_writes_url_token_pid_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, on=True)

    found = read_discovery()
    assert found is not None
    assert found["url"] == URL
    assert found["pid"] == os.getpid()
    assert found["full"] is False
    assert len(found["token"]) >= 40  # secrets.token_urlsafe(32)
    assert found["token"] == app.state.desktop_bridge.token
    assert set(found) == {"url", "token", "pid", "version", "full"}


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX mode bits; Windows relies on the profile ACL"
)
def test_only_the_user_can_read_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app(tmp_path, monkeypatch, on=True)

    mode = stat.S_IMODE(bridge_path().stat().st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(bridge_path().parent.stat().st_mode) & 0o077 == 0


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "d" / "desktop-bridge.json"
    write_discovery(target, {"url": URL, "token": "t" * 43})
    write_discovery(target, {"url": URL, "token": "u" * 43})

    assert json.loads(target.read_text(encoding="utf-8"))["token"] == "u" * 43
    assert sorted(p.name for p in target.parent.iterdir()) == ["desktop-bridge.json"]


def test_a_wrong_token_is_401_and_the_right_one_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, on=True)
    token = app.state.desktop_bridge.token

    with TestClient(app) as client:
        assert client.get("/api/bridge/status").status_code == 401
        assert client.get("/api/bridge/status", headers=_bearer("x" * 43)).status_code == 401
        ok = client.get("/api/bridge/status", headers=_bearer(token))
    assert ok.status_code == 200
    assert ok.json()["tier"] == "operate"


def test_the_spa_is_untouched_it_needs_no_bridge_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, on=True)

    with TestClient(app) as client:
        assert client.get("/api/config").status_code == 200
        assert client.get("/api/code/sessions").status_code == 200


def test_saving_the_switch_off_deletes_the_file_and_kills_the_token_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, on=True)
    old = app.state.desktop_bridge.token

    with TestClient(app) as client:
        saved = client.patch("/api/config", json={"CHIMERA_DESKTOP_BRIDGE": "false"})
        assert saved.status_code == 200
        assert not bridge_path().exists()
        assert client.get("/api/bridge/status", headers=_bearer(old)).status_code == 403

        # On again: a NEW token. Whoever read the old file holds nothing.
        client.patch("/api/config", json={"CHIMERA_DESKTOP_BRIDGE": "true"})
        found = read_discovery()
        assert found is not None and found["token"] != old
        assert client.get("/api/bridge/status", headers=_bearer(old)).status_code == 401
        assert client.get("/api/bridge/status", headers=_bearer(found["token"])).status_code == 200
    get_settings.cache_clear()


def test_full_control_is_written_to_the_file_and_keeps_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, on=True)
    token = app.state.desktop_bridge.token

    with TestClient(app) as client:
        client.patch("/api/config", json={"CHIMERA_DESKTOP_BRIDGE_FULL": "true"})
        found = read_discovery()
        assert found is not None and found["full"] is True and found["token"] == token
        assert client.get("/api/bridge/status", headers=_bearer(token)).json()["tier"] == "full"
    get_settings.cache_clear()


def test_full_control_alone_opens_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app(tmp_path, monkeypatch, on=False, full=True)

    assert not bridge_path().exists()


def test_closing_removes_our_file_and_never_another_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, on=True)
    bridge: DesktopBridge = app.state.desktop_bridge

    bridge.close()
    assert not bridge_path().exists()
    assert bridge.token is None

    # A second app wrote its own file since: ours must not delete it on the way out.
    bridge.sync()
    write_discovery(
        bridge_path(), {"url": "http://127.0.0.1:65002", "token": "other" * 9, "pid": 1}
    )
    bridge.close()
    assert read_discovery() is not None and read_discovery()["token"] == "other" * 9  # type: ignore[index]


def test_a_file_left_on_our_port_by_a_crashed_app_is_cleared_even_when_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_discovery(bridge_path(), {"url": URL, "token": "stale" * 9, "pid": 999_999_999})

    _app(tmp_path, monkeypatch, on=False)

    assert not bridge_path().exists()


def test_the_status_says_what_the_app_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch, on=True)

    with TestClient(app) as client:
        body = client.get(
            "/api/bridge/status", headers=_bearer(app.state.desktop_bridge.token)
        ).json()
    assert body["app"] == "chimera-desktop"
    assert body["default_model"] == get_settings().default_model
    assert body["pending_approvals"] == 0
    assert body["spent_today_usd"] == 0.0
    get_settings.cache_clear()
