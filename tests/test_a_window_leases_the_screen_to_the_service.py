"""A background service approves without a code only while the app that started it leases it a screen.

S1, PR 3: the desktop app now attaches to ``chimera service`` instead of always owning a child. A
service holds no screen credential of its own (#950: ``publish_service`` revokes it), so without
this every click on the app's approval card would turn into "needs the code". The app leases one
to the service it spawned, per window, over ``POST /api/service/window`` — and the whole point is
WHO may take that lease (`chimera/api/service_windows.py`):

* a holder of ``service.token`` alone — the agent's shell after a ``cat`` — must NOT be able to
  lease a credential and then approve its own question without the code; that is the hole #950
  closed, and the test that pins it is the first one below;
* only a request carrying the launcher key the shell gave the service at spawn, in the child's
  environment, may;
* a service the current shell did not start holds no key, and says so (``no_launcher``), and the
  approval card is told why (``no_screen_here``) instead of the false "this is not the screen";
* the lease ends with the window — on disconnect, or when the heartbeat stops;
* the credential never reaches disk, ``/api/info``, a route's answer or a child;
* the desktop bridge ("Allow Claude") is published only while a window is connected.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import screen_credential as sc
from chimera.api.bridge_discovery import read_discovery
from chimera.api.service_windows import ServiceWindows
from chimera.config import Settings, get_settings
from chimera.core.service_record import load_or_create_token, read_record
from chimera.governance import pending
from chimera.interface import ChatSession
from chimera.sandbox.local import _child_env

WAIT = 20.0


class _Asked:
    """A question this process asked — what a turn in the service does."""

    def __init__(self, home: Path) -> None:
        self.result: bool | None = None

        def run() -> None:
            self.result = pending.ask_durably(
                home, "run_shell\ngit push --force", "policy: force push",
                on_asked=lambda *_: None, wait_seconds=WAIT, poll_seconds=0.05,
            )

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline and not pending.pending(home):
            time.sleep(0.01)
        assert pending.pending(home), "precondition: the question was written"
        self.id = pending.pending(home)[0].id
        assert pending.approvable_here(self.id), "precondition: this process holds its code"
        self.home = home

    def waiting(self) -> bool:
        return pending.is_waiting(self.home, self.id)

    def outcome(self) -> bool | None:
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), "the asker never resolved"
        return self.result


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    folder = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(folder))
    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(tmp_path / "svc"))
    monkeypatch.setenv("CHIMERA_BRIDGE_DIR", str(tmp_path / "svc"))
    for name in (sc.ENV_VAR, sc.LAUNCHER_ENV, "CHIMERA_TAURI_SIDECAR", "CHIMERA_DESKTOP_BRIDGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    get_settings.cache_clear()
    yield folder
    get_settings.cache_clear()


class _Service:
    """A built app published as a service, the way `_run_app_server` does in service mode."""

    def __init__(self, home: Path, *, launcher_key: str | None, bridge_on: bool = False) -> None:
        from chimera.cli.service_cmd import publish_service

        self.token = load_or_create_token(home)
        os.environ["CHIMERA_SERVER_TOKEN"] = self.token
        if bridge_on:
            os.environ["CHIMERA_DESKTOP_BRIDGE"] = "1"
        get_settings.cache_clear()
        settings = Settings(
            CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=self.token,
            CHIMERA_DESKTOP_BRIDGE="true" if bridge_on else "false",
        )
        self.app = build_api_app(
            lambda: ChatSession(None),  # type: ignore[arg-type]  # no turn runs here
            workspace=home.parent, settings=settings,
        )
        self.app.state.request_shutdown = lambda: None
        self.app.state.service_info = {"mode": "service", "id": None, "started_at": None}
        self.publication = publish_service(
            self.app, url="http://127.0.0.1:9", token=self.token, home=home,
            bridge="http://127.0.0.1:9", launcher_key=launcher_key,
        )

    def close(self) -> None:
        self.publication.close()
        os.environ.pop("CHIMERA_DESKTOP_BRIDGE", None)


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _window(client: TestClient, token: str, screen: str | None, launcher: str | None = None) -> dict[str, Any]:
    headers = _bearer(token)
    if launcher is not None:
        headers[sc.LAUNCHER_HEADER_NAME] = launcher
    got = client.post("/api/service/window", json={"window": "main", "screen": screen}, headers=headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _approve(client: TestClient, token: str, request_id: str, screen: str | None) -> dict[str, Any]:
    headers = _bearer(token)
    if screen is not None:
        headers[sc.HEADER_NAME] = screen
    got = client.post(f"/api/approvals/{request_id}", json={"approved": True}, headers=headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _secret() -> str:
    return secrets.token_hex(32)


def test_the_service_token_alone_cannot_lease_a_screen_and_approve_without_the_code(home: Path) -> None:
    """The hole this whole design exists to keep shut. The attacker holds what a `cat` gives it —
    `service.token` — and draws its own "screen credential"; the service was started by the app,
    so it HAS a launcher key, just not one the attacker knows."""
    service = _Service(home, launcher_key=_secret())
    try:
        asked = _Asked(home)
        forged = _secret()
        with TestClient(service.app) as client:
            for launcher in (None, "", _secret(), service.token):
                answer = _window(client, service.token, forged, launcher)
                assert answer["screen"] is False and answer["reason"] == "not_the_launcher", answer
                assert forged not in str(answer)
                refused = _approve(client, service.token, asked.id, forged)
                assert refused["outcome"] == "needs_code" and refused["reason"] == "no_screen_here"
                assert asked.waiting(), "a token holder approved the agent's question without the code"
            assert client.post(
                f"/api/approvals/{asked.id}", json={"approved": False}, headers=_bearer(service.token)
            ).json() == {"ok": True}
        assert asked.outcome() is False
    finally:
        service.close()


def test_the_shell_that_started_the_service_leases_its_screen_and_the_card_approves(home: Path) -> None:
    key, screen = _secret(), _secret()
    service = _Service(home, launcher_key=key)
    try:
        asked = _Asked(home)
        with TestClient(service.app) as client:
            # Before the lease: the same click takes the code, and says why.
            before = _approve(client, service.token, asked.id, screen)
            assert before["outcome"] == "needs_code" and before["reason"] == "no_screen_here"
            leased = _window(client, service.token, screen, key)
            assert leased == {"windows": 1, "screen": True, "reason": None, "lease_seconds": 30.0}
            # A stranger's refused attempt does not cost the real window its lease.
            assert _window(client, service.token, _secret(), _secret())["reason"] == "not_the_launcher"
            assert _approve(client, service.token, asked.id, screen) == {"ok": True}
        assert asked.outcome() is True
    finally:
        service.close()


def test_the_lease_ends_when_the_window_disconnects(home: Path) -> None:
    key, screen = _secret(), _secret()
    service = _Service(home, launcher_key=key)
    try:
        asked = _Asked(home)
        with TestClient(service.app) as client:
            assert _window(client, service.token, screen, key)["screen"] is True
            gone = client.delete("/api/service/window/main", headers=_bearer(service.token))
            assert gone.status_code == 200 and gone.json()["windows"] == 0
            after = _approve(client, service.token, asked.id, screen)
            assert after["outcome"] == "needs_code" and after["reason"] == "no_screen_here"
            assert asked.waiting()
            client.post(f"/api/approvals/{asked.id}", json={"approved": False}, headers=_bearer(service.token))
        assert asked.outcome() is False
    finally:
        service.close()


def test_a_service_the_app_did_not_start_says_so_and_never_leases() -> None:
    """`chimera service start` from a terminal (or the app's previous run): no key this shell knows."""
    screen = sc.ScreenCredential()
    windows = ServiceWindows(
        screen, launcher_key=None, bearer=lambda: "t", on_first=lambda: None, on_last=lambda: None
    )
    answer = windows.connect("main", screen=_secret(), launcher=_secret())
    assert (answer.screen, answer.reason, answer.windows) == (False, "no_launcher", 1)
    assert not screen.held() and not windows.launched_here


def test_a_screen_that_could_be_on_disk_is_not_leased() -> None:
    key, bearer = _secret(), _secret()
    screen = sc.ScreenCredential()
    windows = ServiceWindows(
        screen, launcher_key=key, bearer=lambda: bearer, on_first=lambda: None, on_last=lambda: None
    )
    for offered in (bearer, key, "short"):
        assert windows.connect("main", screen=offered, launcher=key).reason == "bad_screen"
    assert not screen.held()


def test_a_window_that_stops_renewing_is_forgotten_with_its_lease_and_the_bridge() -> None:
    now = [100.0]
    events: list[str] = []
    key, value = _secret(), _secret()
    screen = sc.ScreenCredential()
    windows = ServiceWindows(
        screen, launcher_key=key, bearer=lambda: None,
        on_first=lambda: events.append("publish"), on_last=lambda: events.append("retract"),
        lease=30.0, clock=lambda: now[0],
    )
    assert windows.connect("main", screen=value, launcher=key).screen
    assert events == ["publish"]
    now[0] = 125.0
    windows.sweep()
    assert windows.windows() == 1 and screen.value == value, "renewed in time: nothing changes"
    # A heartbeat renews both; then nothing for longer than the lease.
    windows.connect("main", screen=value, launcher=key)
    now[0] = 156.0
    windows.sweep()
    assert windows.windows() == 0 and screen.value is None
    assert events == ["publish", "retract"]
    # The monotonic deadline is checked on use too, between two sweeps.
    windows.connect("main", screen=value, launcher=key)
    now[0] = 200.0
    assert not screen.held(now[0])


def test_the_bridge_is_published_only_while_a_window_is_connected(home: Path, tmp_path: Path) -> None:
    bridge_file = tmp_path / "svc" / "desktop-bridge.json"
    key = _secret()
    service = _Service(home, launcher_key=key, bridge_on=True)
    try:
        with TestClient(service.app) as client:
            assert not bridge_file.exists(), "a service with no window published the bridge"
            # A Settings save syncs the bridge; with no window it still publishes nothing.
            service.app.state.desktop_bridge.sync()
            assert not bridge_file.exists()
            # Presence needs only the owner's token (no lease): it is what the bridge follows.
            assert _window(client, service.token, None)["windows"] == 1
            on_disk = read_discovery(bridge_file)
            assert on_disk is not None and on_disk["url"] == "http://127.0.0.1:9"
            assert client.delete("/api/service/window/main", headers=_bearer(service.token)).status_code == 200
            assert not bridge_file.exists(), "the last window went and the bridge stayed"
            service.app.state.desktop_bridge.sync()
            assert not bridge_file.exists()
    finally:
        service.close()


def test_the_window_routes_are_for_a_service_and_behind_the_token(home: Path) -> None:
    service = _Service(home, launcher_key=_secret())
    try:
        with TestClient(service.app) as client:
            assert client.post("/api/service/window", json={"window": "main"}).status_code == 401
            bad = client.post("/api/service/window", json={"window": "a b"}, headers=_bearer(service.token))
            assert bad.status_code == 400
    finally:
        service.close()
    os.environ["CHIMERA_SERVER_TOKEN"] = ""
    get_settings.cache_clear()
    plain = build_api_app(
        lambda: ChatSession(None),  # type: ignore[arg-type]
        workspace=home.parent, settings=Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json"),
    )
    with TestClient(plain) as client:
        assert client.post("/api/service/window", json={"window": "main"}).status_code == 409


def test_the_leased_credential_and_the_key_never_reach_disk_info_or_a_child(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key, screen = _secret(), _secret()
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.LAUNCHER_ENV, key)
    taken = sc.take_launcher_key()
    assert taken == key and sc.LAUNCHER_ENV not in os.environ, "the key outlived the read"
    monkeypatch.setenv(sc.LAUNCHER_ENV, key)
    assert sc.LAUNCHER_ENV not in _child_env(), "a child would inherit the launcher key"
    service = _Service(home, launcher_key=taken)
    try:
        with TestClient(service.app) as client:
            answer = _window(client, service.token, screen, key)
            assert answer["screen"] is True
            info = client.get("/api/info", headers=_bearer(service.token))
            assert info.status_code == 200 and screen not in info.text and key not in info.text
        record = read_record()
        assert record is not None and screen not in str(record.to_dict())
    finally:
        service.close()
    leaked = [
        str(path) for path in tmp_path.rglob("*")
        if path.is_file() and (screen.encode() in path.read_bytes() or key.encode() in path.read_bytes())
    ]
    assert leaked == [], f"a secret was written to {leaked}"


def test_a_launcher_key_from_anything_but_the_desktop_shell_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_TAURI_SIDECAR", raising=False)
    monkeypatch.setenv(sc.LAUNCHER_ENV, _secret())
    assert sc.take_launcher_key() is None and sc.LAUNCHER_ENV not in os.environ
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.LAUNCHER_ENV, "too-short")
    assert sc.take_launcher_key() is None


def test_closing_the_service_ends_every_lease_and_the_bridge(home: Path, tmp_path: Path) -> None:
    key, screen = _secret(), _secret()
    service = _Service(home, launcher_key=key, bridge_on=True)
    credential = service.app.state.screen_credential
    with TestClient(service.app) as client:
        assert _window(client, service.token, screen, key)["screen"] is True
    assert (tmp_path / "svc" / "desktop-bridge.json").exists()
    service.close()
    assert credential.value is None
    assert not (tmp_path / "svc" / "desktop-bridge.json").exists()


def test_stopping_with_drain_waits_for_the_running_work_and_then_stops(home: Path) -> None:
    """What the app does to an older service before it starts the new one, and before an update
    installs: `POST /api/service/stop?drain=1` answers how much it waits for, and stops after it."""
    service = _Service(home, launcher_key=_secret())
    stopped = threading.Event()
    service.app.state.request_shutdown = stopped.set
    running = service.app.state.run_cancels
    running["a-run-still-going"] = threading.Event()
    try:
        with TestClient(service.app) as client:
            answer = client.post("/api/service/stop?drain=1", headers=_bearer(service.token))
            assert answer.status_code == 200 and answer.json() == {"stopping": True, "draining": 1}
            assert not stopped.wait(1.0), "it stopped with a run still going"
            del running["a-run-still-going"]
            assert stopped.wait(5.0), "the run finished and the service never stopped"
            stopped.clear()
            now = client.post("/api/service/stop?drain=0", headers=_bearer(service.token))
            assert now.json() == {"stopping": True, "draining": 0} and stopped.wait(5.0)
    finally:
        running.pop("a-run-still-going", None)
        service.close()


def test_the_key_the_shell_spawned_the_service_with_is_the_one_it_leases_to(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real path, end to end: the shell puts the key in the child's environment, the app's
    build takes it out (in every mode), and the service's windows use it without being told."""
    from chimera.cli.service_cmd import publish_service

    key, screen = _secret(), _secret()
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.LAUNCHER_ENV, key)
    token = load_or_create_token(home)
    os.environ["CHIMERA_SERVER_TOKEN"] = token
    get_settings.cache_clear()
    app = build_api_app(
        lambda: ChatSession(None),  # type: ignore[arg-type]
        workspace=home.parent,
        settings=Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=token),
    )
    assert sc.LAUNCHER_ENV not in os.environ, "the build left the key for a child to find"
    app.state.request_shutdown = lambda: None
    app.state.service_info = {"mode": "service", "id": None, "started_at": None}
    publication = publish_service(app, url="http://127.0.0.1:9", token=token, home=home, bridge="http://127.0.0.1:9")
    try:
        assert app.state.service_windows.launched_here
        with TestClient(app) as client:
            assert _window(client, token, screen, key)["screen"] is True
            assert _window(client, token, _secret(), _secret())["reason"] == "not_the_launcher"
    finally:
        publication.close()
