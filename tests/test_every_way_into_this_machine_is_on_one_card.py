"""Every way into this machine is on one card, and the card shows none of the keys (study 29, P5.5).

The doors existed and were scattered: the bearer token on the General tab, the bridge's token
visible nowhere, each share link inside its own conversation's Share dialog, and the LAN door
inside that same dialog. `GET /api/security/access` reads all of them, and its controls only
narrow: revoke one link, revoke every link, give the bridge a new token. Two settings narrow
sharing itself — `CHIMERA_SHARING` (off refuses a new link, closes the door, and stops every link
from opening) and `CHIMERA_SHARE_EXPIRY_HOURS` (a new link stops opening after that long).

What is pinned here:

* no response of the new routes carries more than four characters of ANY token on the machine —
  the server's, the bridge's before and after a rotation, every share link's — checked by scanning
  every body for every five-character slice of every token;
* an expired link opens nothing, on every guest route, and is still listed (as expired) so the
  owner sees it expired instead of watching it vanish;
* the defaults are what sharing did before: on, and links that never expire;
* sharing off refuses a new link and the door, makes every existing link inert, and closes an open
  door at the moment it is saved; on again, the links work again;
* the bridge can neither read these routes nor write the two sharing settings, full control or not.
"""

from __future__ import annotations

import hashlib
import json
import time as real_time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import chimera.api.sharing as sharing
from chimera.api.bridge_routes import OWNER_ONLY_SETTINGS, ROUTES
from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.api.desktop_bridge import read_discovery
from chimera.api.sharing import ShareStore
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession
from tests.test_a_conversation_can_be_shared_by_a_token_that_reaches_only_it import (
    _Agent,
    _session_of,
)

URL = "http://127.0.0.1:65002"
# Derived, not written out. A random-looking literal assigned to a name ending in TOKEN is exactly
# what gitleaks' `generic-api-key` rule reports, and the first version of this line was reported on
# main's history (see .gitleaksignore). The test needs a token with many distinct five-character
# slices for `_no_token_in` to mean something, so a low-entropy placeholder would weaken it; a
# deterministic digest keeps the strength and leaves no key-shaped string in the source.
SERVER_TOKEN = "srv-" + hashlib.sha256(b"the server token of this test").hexdigest()[:32]


def _build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    frozen: bool = True,
    **env: str,
) -> TestClient:
    """A real app with a stub agent. ``frozen`` injects the settings (fixed for the app's life);
    otherwise the app reads the live settings, so a PATCH from the Settings screen is seen."""
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    # Owned before anything writes them: `patch_config` sets os.environ for real.
    for key in ("CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS", "CHIMERA_SERVER_TOKEN"):
        monkeypatch.setenv(key, "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings() if frozen else None
    app = build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings)
    app.state.desktop_bridge.attach(URL)
    client = TestClient(app)
    if env.get("CHIMERA_SERVER_TOKEN"):
        client.headers["Authorization"] = f"Bearer {env['CHIMERA_SERVER_TOKEN']}"
    return client


def _no_token_in(body: str, tokens: list[str]) -> None:
    """At most four characters of any token: no five-character slice of one appears in ``body``."""
    for token in tokens:
        assert len(token) >= 8, "a test token too short to mean anything"
        for i in range(len(token) - 4):
            piece = token[i : i + 5]
            assert piece not in body, f"five characters of a token ({piece!r}) in {body[:200]!r}"


def _clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Move the sharing module's wall clock; the bus keeps the real monotonic one."""
    now = [real_time.time()]
    monkeypatch.setattr(
        sharing, "time", SimpleNamespace(time=lambda: now[0], monotonic=real_time.monotonic)
    )
    return now


# ------------------------------------------------------------------ the card, and no secret on it


def test_no_response_of_the_new_routes_carries_more_than_four_characters_of_any_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(
        tmp_path,
        monkeypatch,
        CHIMERA_SERVER_TOKEN=SERVER_TOKEN,
        CHIMERA_DESKTOP_BRIDGE="true",
        CHIMERA_SHARE_EXPIRY_HOURS="24",
    )
    app: Any = client.app
    sid = _session_of(client, "fix the login page")
    other = _session_of(client, "another conversation")
    minted = [
        client.post(f"/api/code/sessions/{sid}/share", json={"label": "Ana"}).json(),
        client.post(f"/api/code/sessions/{sid}/share", json={"label": "Ana"}).json(),
        client.post(f"/api/code/sessions/{other}/share", json={"label": "Bia"}).json(),
    ]
    share_tokens = [m["token"] for m in minted]
    bridge_before = str(app.state.desktop_bridge.token)
    tokens = [SERVER_TOKEN, bridge_before, *share_tokens]
    door = app.state.guest_server
    bodies: list[str] = []
    try:
        assert client.post("/api/code/share/network", json={"port": 0}).status_code == 200

        card = client.get("/api/security/access")
        assert card.status_code == 200
        bodies.append(card.text)
        data = card.json()
        # Every door is on it.
        assert data["server_token"] == {"set": True}
        assert data["bridge"]["enabled"] is True and data["bridge"]["active"] is True
        assert data["bridge"]["tier"] == "operate"
        assert data["bridge"]["hint"] == f"…{bridge_before[-4:]}"
        assert data["sharing"] == {"enabled": True, "expiry_hours": 24.0}
        assert data["guest_door"]["open"] is True and data["guest_door"]["port"] == door.port
        assert all("?t=" not in url for url in data["guest_door"]["urls"])
        links = data["links"]
        assert [link["session_id"] for link in links] == [other, sid, sid]  # newest first
        assert [link["hint"] for link in links] == [f"…{t[-4:]}" for t in reversed(share_tokens)]
        assert links[2]["session_title"] == "fix the login page" and links[2]["label"] == "Ana"
        assert all(link["expired"] is False and link["expires_at"] for link in links)
        assert len({link["id"] for link in links}) == 3

        rotated = client.post("/api/security/access/bridge/rotate")
        assert rotated.status_code == 200
        bodies.append(rotated.text)
        bridge_after = str(app.state.desktop_bridge.token)
        assert bridge_after != bridge_before
        tokens.append(bridge_after)
        assert rotated.json()["hint"] == f"…{bridge_after[-4:]}"

        one = client.delete(f"/api/security/access/links/{links[0]['id']}")
        bodies.append(one.text)
        assert one.json() == {"ok": True}
        missing = client.delete("/api/security/access/links/not-an-id")
        bodies.append(missing.text)
        assert missing.json() == {"ok": False}
        bodies.append(client.get("/api/security/access").text)
        everything = client.delete("/api/security/access/links")
        bodies.append(everything.text)
        assert everything.json() == {"revoked": 2}
        bodies.append(client.get("/api/security/access").text)
    finally:
        client.delete("/api/code/share/network")
    assert len(bodies) == 7
    for body in bodies:
        _no_token_in(body, tokens)


def test_the_card_and_its_controls_need_the_server_token_when_one_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(tmp_path, monkeypatch, CHIMERA_SERVER_TOKEN=SERVER_TOKEN)
    del client.headers["Authorization"]
    assert client.get("/api/security/access").status_code == 401
    assert client.post("/api/security/access/bridge/rotate").status_code == 401
    assert client.delete("/api/security/access/links").status_code == 401
    assert client.delete("/api/security/access/links/x").status_code == 401


def test_the_bridge_reaches_none_of_the_card_and_may_not_write_the_sharing_settings() -> None:
    assert not any(route.path.startswith("/api/security") for route in ROUTES.values())
    assert {"CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS"} <= OWNER_ONLY_SETTINGS
    assert {"CHIMERA_DESKTOP_BRIDGE", "CHIMERA_DESKTOP_BRIDGE_FULL"} <= OWNER_ONLY_SETTINGS


def test_a_bridge_with_full_control_cannot_turn_sharing_back_on_or_lengthen_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(
        tmp_path,
        monkeypatch,
        frozen=False,
        CHIMERA_DESKTOP_BRIDGE="true",
        CHIMERA_DESKTOP_BRIDGE_FULL="true",
        CHIMERA_SHARING="false",
        CHIMERA_SHARE_EXPIRY_HOURS="1",
    )
    app: Any = client.app
    bearer = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    for body in ({"CHIMERA_SHARING": "true"}, {"CHIMERA_SHARE_EXPIRY_HOURS": ""}):
        refused = client.post(
            "/api/bridge/call", json={"route": "settings.edit", "body": body}, headers=bearer
        )
        assert refused.status_code == 403, body
    assert not (tmp_path / ".env").exists() or "SHAR" not in (tmp_path / ".env").read_text("utf-8")
    get_settings.cache_clear()
    assert get_settings().sharing is False and get_settings().share_expiry_hours == 1.0


# ------------------------------------------------------------------ expiry


def test_an_expired_link_does_not_open_its_conversation_on_any_guest_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = _clock(monkeypatch)
    client = _build(tmp_path, monkeypatch, CHIMERA_SHARE_EXPIRY_HOURS="1")
    sid = _session_of(client, "hello")
    minted = client.post(f"/api/code/sessions/{sid}/share").json()
    token = minted["token"]
    assert minted["expires_at"] == pytest.approx(now[0] + 3600)
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/guest/api/session", headers=bearer).status_code == 200

    now[0] += 3599
    assert client.get("/guest/api/session", headers=bearer).status_code == 200
    now[0] += 2  # past the hour
    assert client.get("/guest/api/session", headers=bearer).status_code == 401
    assert client.get("/guest/api/session", params={"t": token}).status_code == 401
    assert client.get("/guest/api/presence", headers=bearer).status_code == 401
    assert client.get("/guest/api/live", params={"t": token}).status_code == 401
    turn = client.post("/guest/api/turn", json={"message": "still here?"}, headers=bearer)
    assert turn.status_code == 401
    assert [e["you"] for e in client.get(f"/api/code/sessions/{sid}").json()["exchanges"]] == ["hello"]

    # Not offered again as a link to copy, but still on the card — expired, not vanished.
    assert client.get(f"/api/code/sessions/{sid}/shares").json() == {"shares": []}
    listed = client.get("/api/security/access").json()["links"]
    assert len(listed) == 1 and listed[0]["expired"] is True
    # And the owner can clear it, from the Share dialog's revoke as from the card.
    assert client.delete(f"/api/code/sessions/{sid}/shares/{token}").json() == {"ok": True}
    assert client.get("/api/security/access").json()["links"] == []


def test_the_store_refuses_an_expired_token_and_keeps_a_link_from_before_expiry_existed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = _clock(monkeypatch)
    path = tmp_path / "code_shares.json"
    # A file written before `expires_at` existed: its links never expire.
    path.write_text(
        json.dumps([{"token": "old-token-from-before-expiry", "session_id": "s", "created_at": 1.0}]),
        encoding="utf-8",
    )
    store = ShareStore(path)
    assert store.resolve("old-token-from-before-expiry") is not None
    short = store.mint("s", expires_in=60)
    never = store.mint("s")
    assert never.expires_at is None and store.mint("s", expires_in=0).expires_at is None
    now[0] += 61
    assert store.resolve(short.token) is None
    assert store.resolve(never.token) is not None
    assert store.resolve("old-token-from-before-expiry") is not None
    # Persisted with its expiry: a second store over the file agrees.
    assert ShareStore(path).resolve(short.token) is None
    live = [s.token for s in store.for_session("s")]
    assert len(live) == 3 and short.token not in live and never.token in live
    assert short.token in [s.token for s in store.for_session("s", include_expired=True)]
    assert len(store.all()) == 4


def test_the_defaults_are_what_sharing_did_before_on_and_never_expiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Settings.model_fields["sharing"].default is True
    assert Settings.model_fields["share_expiry_hours"].default is None
    client = _build(tmp_path, monkeypatch)
    sid = _session_of(client, "hello")
    minted = client.post(f"/api/code/sessions/{sid}/share").json()
    assert minted["expires_at"] is None
    assert client.get("/guest/api/session", params={"t": minted["token"]}).status_code == 200


# ------------------------------------------------------------------ the switch


def test_sharing_off_refuses_a_link_and_the_door_closes_the_open_door_and_stills_every_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(tmp_path, monkeypatch, frozen=False, CHIMERA_SHARING="true")
    app: Any = client.app
    door = app.state.guest_server
    sid = _session_of(client, "hello")
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    try:
        assert client.post("/api/code/share/network", json={"port": 0}).json()["open"] is True

        assert client.patch("/api/config", json={"CHIMERA_SHARING": "false"}).status_code == 200
        assert door.open is False  # closed at the moment it was saved
        assert client.get("/guest/api/session", params={"t": token}).status_code == 401
        refused = client.post(f"/api/code/sessions/{sid}/share")
        assert refused.status_code == 403 and "Sharing" in refused.json()["detail"]
        assert client.post("/api/code/share/network", json={"port": 0}).status_code == 403
        assert door.open is False
        card = client.get("/api/security/access").json()
        assert card["sharing"]["enabled"] is False and len(card["links"]) == 1  # kept, not deleted

        assert client.patch("/api/config", json={"CHIMERA_SHARING": "true"}).status_code == 200
        assert client.get("/guest/api/session", params={"t": token}).status_code == 200
    finally:
        client.delete("/api/code/share/network")
        get_settings.cache_clear()


# ------------------------------------------------------------------ the controls


def test_revoking_from_the_card_closes_one_link_one_conversation_or_every_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(tmp_path, monkeypatch)
    a = _session_of(client, "first")
    b = _session_of(client, "second")
    tokens = {
        "a1": client.post(f"/api/code/sessions/{a}/share").json()["token"],
        "a2": client.post(f"/api/code/sessions/{a}/share").json()["token"],
        "b1": client.post(f"/api/code/sessions/{b}/share").json()["token"],
        "b2": client.post(f"/api/code/sessions/{b}/share").json()["token"],
    }

    def opens(name: str) -> bool:
        return client.get("/guest/api/session", params={"t": tokens[name]}).status_code == 200

    card = client.get("/api/security/access").json()["links"]
    by_hint = {link["hint"]: link["id"] for link in card}
    assert client.delete(f"/api/security/access/links/{by_hint['…' + tokens['a1'][-4:]]}").json() == {
        "ok": True
    }
    assert [opens(n) for n in ("a1", "a2", "b1", "b2")] == [False, True, True, True]

    assert client.delete("/api/security/access/links", params={"session_id": b}).json() == {
        "revoked": 2
    }
    assert [opens(n) for n in ("a1", "a2", "b1", "b2")] == [False, True, False, False]

    assert client.delete("/api/security/access/links").json() == {"revoked": 1}
    assert not any(opens(n) for n in tokens)
    assert client.get("/api/security/access").json()["links"] == []
    assert client.delete("/api/security/access/links").json() == {"revoked": 0}


def test_a_new_bridge_token_stops_the_old_one_at_once_and_keeps_full_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(
        tmp_path, monkeypatch, CHIMERA_DESKTOP_BRIDGE="true", CHIMERA_DESKTOP_BRIDGE_FULL="true"
    )
    app: Any = client.app
    old = str(app.state.desktop_bridge.token)
    assert client.get("/api/bridge/status", headers={"Authorization": f"Bearer {old}"}).status_code == 200

    rotated = client.post("/api/security/access/bridge/rotate").json()
    new = str(app.state.desktop_bridge.token)
    assert new != old and rotated["tier"] == "full" and rotated["active"] is True
    assert client.get("/api/bridge/status", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert client.get("/api/bridge/status", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    found = read_discovery()
    assert found is not None and found["token"] == new and found["full"] is True


def test_there_is_no_bridge_token_to_rotate_while_the_bridge_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(tmp_path, monkeypatch)
    app: Any = client.app
    assert client.post("/api/security/access/bridge/rotate").status_code == 409
    assert app.state.desktop_bridge.token is None and read_discovery() is None
    bridge = client.get("/api/security/access").json()["bridge"]
    assert bridge == {"enabled": False, "active": False, "tier": None, "hint": ""}


# ------------------------------------------------------------------ the settings rows


def test_the_settings_screen_may_write_both_and_a_value_that_would_mislead_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS"):
        assert is_editable(key)
        assert key not in APPLIES_WHEN  # read per request: it applies at once
        monkeypatch.setenv(key, "")
    env = tmp_path / ".env"
    for bad in ("0", "-2", "soon", "nan", "inf"):
        with pytest.raises(ValueError):
            patch_config({"CHIMERA_SHARE_EXPIRY_HOURS": bad}, env_path=env)
    with pytest.raises(ValueError):
        patch_config({"CHIMERA_SHARING": "maybe"}, env_path=env)
    assert not env.exists()  # nothing written by a refused save

    patch_config({"CHIMERA_SHARE_EXPIRY_HOURS": "48", "CHIMERA_SHARING": "false"}, env_path=env)
    assert read_config(get_settings())["sharing"] == {"enabled": False, "expiry_hours": 48.0}
    patch_config({"CHIMERA_SHARE_EXPIRY_HOURS": ""}, env_path=env)
    assert read_config(get_settings())["sharing"]["expiry_hours"] is None
    get_settings.cache_clear()


@pytest.mark.parametrize(("written", "read"), [("", None), ("0", None), ("-1", None), ("abc", None), ("12", 12.0)])
def test_a_hand_edited_expiry_never_stops_the_app_from_starting(written: str, read: float | None) -> None:
    assert Settings(CHIMERA_SHARE_EXPIRY_HOURS=written).share_expiry_hours == read
