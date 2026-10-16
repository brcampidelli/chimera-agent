"""S3: a phone paired with a one-time code is the owner for reading and talking — never the screen.

`chimera pair` (and "Pair a device" on Settings > Access) draws a code that works once, for five
minutes, typed into a page served by a LAN listener the owner opened. It becomes a 30-day cookie
signed with a key derived from the owner's device secret. What each test here catches if the rule
breaks:

* a code that works twice, or after five minutes;
* wrong guesses that never run out — per address, and against the live codes themselves;
* a cookie that does not open the owner's reading and talking routes, or one that opens the routes
  that hand out access, change settings or widen the agent's reach;
* a paired device that approves a question without its code (study 30, S30-30), or that is handed
  the bearer or the screen credential by the page;
* a revoke that waits for the cookie to expire, or a stream that outlives its device;
* a new device secret that leaves old cookies working;
* the desktop bridge reaching pairing at all;
* anything listening off loopback before the owner opened it.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import screen_credential as sc
from chimera.api.bridge_routes import ROUTES
from chimera.api.desktop_bridge import asgi_call
from chimera.api.device_door import DeviceGateway
from chimera.api.devices import (
    CODE_SECONDS,
    COOKIE_NAME,
    IP_LIMIT,
    IP_WINDOW_SECONDS,
    MAX_FAILURES,
    Attempts,
    DeviceRegistry,
    PairingCodes,
    device_may_reach,
    shown_code,
)
from chimera.config import Settings, get_settings
from chimera.governance import pending
from chimera.interface import ChatSession

TOKEN = "owner-bearer-token-0123456789"
PHONE = ("192.168.1.20", 50123)
WAIT = 20.0


class _Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


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


def _app(home: Path, *, static_dir: Path | None = None) -> Any:
    os.environ["CHIMERA_SERVER_TOKEN"] = TOKEN
    get_settings.cache_clear()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN)
    return build_api_app(
        lambda: ChatSession(None),  # type: ignore[arg-type]  # no turn runs here
        workspace=home.parent,
        settings=settings,
        screen=sc.for_mode("app", server_token=TOKEN),
        static_dir=static_dir,
    )


def _owner() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


def _phone(app: Any, client: tuple[str, int] = PHONE) -> TestClient:
    """The device door's app, as a phone on the LAN reaches it (no socket: the same ASGI app)."""
    gateway = DeviceGateway(app, app.state.device_registry, watch_seconds=0.05)
    return TestClient(gateway, base_url="http://192.168.1.5:8123", client=client, follow_redirects=False)


def _pair(app: Any, phone: TestClient, name: str = "Bruno's phone") -> str:
    code, _ = app.state.device_registry.codes.mint()
    got = phone.post("/pair", data={"code": shown_code(code), "name": name})
    assert got.status_code == 303, got.text
    assert got.headers["location"] == "/"
    cookie = phone.cookies.get(COOKIE_NAME)
    assert cookie, "no device cookie was set"
    return str(cookie)


# ---- the code ---------------------------------------------------------------------------------


def test_a_code_works_once_and_only_for_five_minutes() -> None:
    clock = _Clock()
    codes = PairingCodes(clock=clock)
    code, expires = codes.mint()
    assert expires == clock.now + CODE_SECONDS == clock.now + 300
    assert codes.redeem(shown_code(code).lower()), "typed in lower case with the dash, it is the code"
    assert not codes.redeem(code), "a code worked twice"

    late, _ = codes.mint()
    clock.now += CODE_SECONDS - 1
    in_time, _ = codes.mint()
    assert codes.redeem(late), "a code stopped working before its five minutes"
    clock.now += 2
    assert not codes.redeem(late) and codes.count() == 1
    clock.now += CODE_SECONDS
    assert not codes.redeem(in_time), "a code worked after five minutes"


def test_wrong_guesses_void_every_live_code_and_shut_out_the_address(home: Path) -> None:
    codes = PairingCodes()
    code, _ = codes.mint()
    for _ in range(MAX_FAILURES):
        assert not codes.redeem("ZZZZ-ZZZZ")
    assert not codes.redeem(code), f"the right code still worked after {MAX_FAILURES} wrong ones"

    clock = _Clock()
    attempts = Attempts(clock=clock)
    for _ in range(IP_LIMIT):
        assert not attempts.blocked("10.0.0.9")
        attempts.fail("10.0.0.9")
    assert attempts.blocked("10.0.0.9") and not attempts.blocked("10.0.0.10")
    clock.now += IP_WINDOW_SECONDS + 1
    assert not attempts.blocked("10.0.0.9")

    # Through the page: the address that guessed is refused even with the right code, unheard.
    app = _app(home)
    registry = app.state.device_registry
    with _phone(app, ("10.0.0.9", 1)) as guesser, _phone(app, ("10.0.0.10", 1)) as other:
        for i in range(IP_LIMIT):
            registry.codes.mint()  # a fresh live code each time, so the address limit is what bites
            got = guesser.post("/pair", data={"code": f"AAAA-AAA{i + 2}"})
            assert got.status_code == 401 and COOKIE_NAME not in guesser.cookies
        good, _ = registry.codes.mint()
        refused = guesser.post("/pair", data={"code": good})
        assert refused.status_code == 429 and COOKIE_NAME not in guesser.cookies
        assert other.post("/pair", data={"code": good}).status_code == 303, "another address paid"


# ---- the cookie ---------------------------------------------------------------------------------


def test_the_pairing_page_and_the_cookie_it_sets(home: Path) -> None:
    app = _app(home)
    with _phone(app) as phone:
        page = phone.get("/pair?code=ABCD-EFGH", headers={"accept-language": "pt-BR,pt;q=0.9"})
        assert page.status_code == 200 and 'value="ABCD-EFGH"' in page.text
        assert "Parear este dispositivo" in page.text
        assert "script-src" not in page.headers["content-security-policy"]
        assert "default-src 'none'" in page.headers["content-security-policy"]
        # Not paired yet: the app redirects to the page, the API says so.
        assert phone.get("/").headers["location"] == "/pair"
        assert phone.get("/api/code/sessions").status_code == 401

        code, _ = app.state.device_registry.codes.mint()
        got = phone.post("/pair", data={"code": code, "name": "  sofa   phone "})
        flags = got.headers["set-cookie"]
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=2592000"):
            assert flag in flags, flags
        assert "Secure" not in flags, "Secure over plain http: the browser would drop the cookie"
        devices = app.state.device_registry.devices()
        assert [(d.name, d.last_ip) for d in devices] == [("sofa phone", "192.168.1.x")]

    with _phone(app) as phone:
        code, _ = app.state.device_registry.codes.mint()
        got = phone.post("/pair", data={"code": code}, headers={"x-forwarded-proto": "https"})
        assert "Secure" in got.headers["set-cookie"], "a page served through a TLS tunnel"


def test_a_paired_cookie_reads_and_talks_as_the_owner_and_no_more(home: Path) -> None:
    app = _app(home)
    with _phone(app) as phone, TestClient(app) as owner:
        _pair(app, phone)
        # Reading, with no bearer at all.
        assert phone.get("/api/code/sessions").status_code == 200
        assert phone.get("/api/health").status_code == 200
        # Talking: an answer to the agent's own question reaches its route (none is waiting here).
        talk = phone.post("/api/questions/q-nothing", json={"answer": "yes"})
        assert talk.status_code == 200, talk.text
        # What a lost phone must not do.
        for method, path in (
            ("PATCH", "/api/config"),
            ("GET", "/api/security/access"),
            ("GET", "/api/security/devices"),
            ("POST", "/api/security/devices/pair"),
            ("POST", "/api/security/devices/door"),
            ("DELETE", "/api/security/devices"),
            ("POST", "/api/code/sessions/s1/share"),
            ("GET", "/api/code/sessions/s1/shares"),
            ("POST", "/api/code/share/network"),
            ("PUT", "/api/code/workspaces/grant"),
            ("POST", "/api/messaging/discord/start"),
            ("DELETE", "/api/code/sessions/s1"),
            ("POST", "/api/approvals/reject-all"),
            ("POST", "/api/service/stop"),
            ("POST", "/api/bridge/call"),
        ):
            got = phone.request(method, path, json={})
            assert got.status_code == 403, f"{method} {path} answered {got.status_code} to a device"
        # A write from another origin, even with the cookie.
        cross = phone.post(
            "/api/questions/q-nothing", json={"answer": "x"}, headers={"origin": "http://evil.example"}
        )
        assert cross.status_code == 403
        # The owner's own token is unaffected, and the device door did not become a back door for it.
        assert owner.get("/api/security/devices", headers=_owner()).status_code == 200
        assert owner.get("/api/code/sessions").status_code == 401


def test_the_policy_table() -> None:
    allowed = [
        ("GET", "/api/code/sessions"),
        ("GET", "/api/code/sessions/abc"),
        ("POST", "/api/code/turn"),
        ("POST", "/api/code/turns/t1/guidance"),
        ("POST", "/api/code/turns/t1/stop"),
        ("POST", "/api/chat/stream"),
        ("POST", "/api/questions/q1"),
        ("POST", "/api/approvals/a1"),
        ("POST", "/api/code/sessions/abc/archive"),
        ("POST", "/api/attachments"),
        ("PUT", "/api/ui/layout"),
    ]
    refused = [
        ("GET", "/api/security/access"),
        ("PATCH", "/api/config"),
        ("POST", "/api/config/pool/openai"),
        ("POST", "/api/approvals/reject-all"),
        ("DELETE", "/api/approval-rules/r1"),
        ("POST", "/api/mcp"),
        ("POST", "/api/cron"),
        ("POST", "/api/code/sessions/abc/undo/t1"),
        ("PATCH", "/api/code/sessions/abc/pull-request"),
        ("PATCH", "/api/shell/prefs"),
        ("POST", "/api/fs/file"),
        ("GET", "/api/code/sessions/abc/shares"),
        ("GET", "/api/bridge/status"),
    ]
    assert [p for p in allowed if device_may_reach(*p)] == []
    assert [p for p in refused if not device_may_reach(*p)] == []


# ---- approvals ----------------------------------------------------------------------------------


class _Asked:
    """A question this process asked — what a turn in the app's own backend does."""

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
        self.id = pending.pending(home)[0].id
        assert pending.approvable_here(self.id), "precondition: this process holds its code"
        self.home = home

    def outcome(self) -> bool | None:
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), "the asker never resolved"
        return self.result


def test_a_paired_device_does_not_approve_without_the_questions_code(home: Path) -> None:
    app = _app(home)
    screen = app.state.screen_credential
    assert screen.value, "precondition: this app has a screen that could approve with one click"
    asked = _Asked(home)
    with _phone(app) as phone:
        _pair(app, phone)
        # Even carrying the screen credential (copied off the owner's page somehow): the door strips
        # it, and the device scope is never the screen.
        for headers in ({}, {sc.HEADER_NAME: screen.value}, {"authorization": f"Bearer {TOKEN}"}):
            got = phone.post(f"/api/approvals/{asked.id}", json={"approved": True}, headers=headers)
            assert got.status_code == 200
            answer = got.json()
            assert answer["ok"] is False and answer["outcome"] == "needs_code", answer
            assert answer["reason"] == "not_the_screen"
            assert pending.is_waiting(home, asked.id), "a device approved without the code"
        # A refusal needs no code, from the phone too.
        assert phone.post(f"/api/approvals/{asked.id}", json={"approved": False}).json() == {"ok": True}
    assert asked.outcome() is False


def test_the_page_never_hands_a_device_the_bearer_or_the_screen(home: Path, tmp_path: Path) -> None:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")
    app = _app(home, static_dir=static)
    # Through a tunnel (`cloudflared`, `tailscale serve`) the device arrives from loopback — exactly
    # where the page would otherwise carry both secrets.
    with _phone(app, ("127.0.0.1", 40000)) as phone, TestClient(app, client=("127.0.0.1", 1)) as local:
        assert 'name="chimera-screen"' in local.get("/").text, "precondition: the loopback page has it"
        _pair(app, phone)
        page = phone.get("/")
        assert page.status_code == 200
        assert "chimera-token" not in page.text and "chimera-screen" not in page.text
        assert TOKEN not in page.text and str(app.state.screen_credential.value) not in page.text


# ---- revoking -----------------------------------------------------------------------------------


def test_revoking_a_device_shuts_it_out_at_once(home: Path) -> None:
    app = _app(home)
    with _phone(app) as phone, _phone(app, ("192.168.1.21", 1)) as tablet, TestClient(app) as owner:
        _pair(app, phone, "phone")
        _pair(app, tablet, "tablet")
        assert phone.get("/api/code/sessions").status_code == 200
        listed = owner.get("/api/security/devices", headers=_owner()).json()["devices"]
        target = next(d["id"] for d in listed if d["name"] == "phone")
        assert owner.delete(f"/api/security/devices/{target}", headers=_owner()).json() == {"ok": True}
        assert phone.get("/api/code/sessions").status_code == 401, "revoked, and it still read"
        assert tablet.get("/api/code/sessions").status_code == 200, "revoking one revoked another"
        # The access card lists the rest, and nothing of a cookie.
        card = owner.get("/api/security/access", headers=_owner()).json()
        assert [d["name"] for d in card["devices"]] == ["tablet"]
        assert str(tablet.cookies.get(COOKIE_NAME)) not in json.dumps(card)


def test_a_stream_ends_when_its_device_is_revoked(home: Path) -> None:
    registry = DeviceRegistry(home)
    frames: list[bytes] = []

    async def forever(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            await send({"type": "http.response.body", "body": b"frame\n", "more_body": True})
            await asyncio.sleep(0.02)

    gateway = DeviceGateway(forever, registry, watch_seconds=0.05)
    registry.codes.mint()
    device, cookie = registry.issue(name="phone", address=PHONE[0])

    async def run() -> float:
        # A hand-driven ASGI call, so the stream is read as it is sent (a buffering client would
        # wait for an end that, without the revoke, never comes).
        scope = {
            "type": "http", "method": "GET", "path": "/api/code/sessions/s1/live",
            "headers": [(b"host", b"192.168.1.5:8123"), (b"cookie", f"{COOKIE_NAME}={cookie}".encode())],
            "client": PHONE, "scheme": "http", "query_string": b"",
        }
        never = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await never.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body":
                frames.append(message["body"])

        async def revoke_soon() -> None:
            await asyncio.sleep(0.3)
            registry.revoke(device.id)

        started = time.monotonic()
        task = asyncio.create_task(revoke_soon())
        await asyncio.wait_for(gateway(scope, receive, send), timeout=5)
        await task
        return time.monotonic() - started

    took = asyncio.run(run())
    assert frames, "precondition: the stream sent frames while the device was paired"
    assert took < 2.0, f"the stream outlived its device by {took:.1f}s"


def test_a_new_device_secret_revokes_every_cookie(home: Path) -> None:
    app = _app(home)
    with _phone(app) as phone, _phone(app, ("192.168.1.21", 1)) as tablet, TestClient(app) as owner:
        _pair(app, phone)
        _pair(app, tablet)
        assert owner.delete("/api/security/devices", headers=_owner()).json() == {"revoked": 2}
        assert phone.get("/api/code/sessions").status_code == 401
        assert tablet.get("/api/code/sessions").status_code == 401

    # And the signature itself: a device put back in the list with an old cookie does not verify.
    registry = DeviceRegistry(home)
    device, cookie = registry.issue(name="phone", address="10.0.0.1")
    assert registry.verify(cookie) is not None
    (home / "devices.key").write_text("f" * 64, encoding="utf-8")
    assert registry.verify(cookie) is None, "a cookie signed by the old secret still verified"
    tampered = cookie.rsplit(".", 1)[0] + ".AAAA"
    assert registry.verify(tampered) is None
    assert device.id in {d.id for d in registry.devices()}, "precondition: still listed"


# ---- who may pair -------------------------------------------------------------------------------


def test_the_bridge_cannot_pair_list_or_revoke(home: Path) -> None:
    assert not [r for r in ROUTES.values() if r.path.startswith("/api/security/")], (
        "a pairing or access route is in the bridge's table"
    )
    app = _app(home)
    with TestClient(app) as client:
        for method, path in (
            ("POST", "/api/security/devices/pair"),
            ("POST", "/api/security/devices/door"),
            ("GET", "/api/security/devices"),
            ("DELETE", "/api/security/devices"),
        ):
            status, raw = client.portal.call(  # type: ignore[union-attr]
                lambda m=method, p=path: asgi_call(app, m, p, body={}, headers=_owner())
            )
            assert status == 403, f"the bridge reached {method} {path}: {status} {raw!r}"
    assert not app.state.device_door.open


def _listening_off_loopback(port: int) -> bool:
    """Whether something answers on this port at an address that is not loopback."""
    from chimera.api.sharing import lan_addresses

    for address in lan_addresses():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((address, port)) == 0:
                return True
    return False


def test_nothing_listens_off_loopback_until_the_owner_opens_the_door(home: Path) -> None:
    app = _app(home)
    door = app.state.device_door
    with TestClient(app) as owner:
        assert not door.open and not app.state.guest_server.open
        state = owner.get("/api/security/devices", headers=_owner()).json()
        assert state["door"] == {"open": False, "port": None, "urls": []}
        refused = owner.post("/api/security/devices/pair", headers=_owner())
        assert refused.status_code == 409, "a code was minted for a door nobody can reach"
        assert app.state.device_registry.codes.count() == 0

        opened = owner.post("/api/security/devices/door", headers=_owner(), json={}).json()
        try:
            assert opened["open"] is True and opened["port"]
            port = int(opened["port"])
            minted = owner.post("/api/security/devices/pair", headers=_owner()).json()
            assert len(minted["code"]) == 9 and minted["code"][4] == "-"
            if minted["urls"]:  # a machine with a LAN address
                assert minted["urls"][0].endswith(f"/pair?code={minted['code']}")
                assert minted["qr"] and len(minted["qr"]) == len(minted["qr"][0])
            # Over the real socket: the page answers, the owner's API does not without a cookie.
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as lan:
                assert lan.get("/pair").status_code == 200
                assert lan.get("/api/code/sessions").status_code == 401
                assert lan.get("/api/code/sessions", headers=_owner()).status_code == 401, (
                    "the owner's bearer opened the API through the device door"
                )
        finally:
            closed = owner.delete("/api/security/devices/door", headers=_owner()).json()
        assert closed["open"] is False
        assert app.state.device_registry.codes.count() == 0, "a code outlived its door"
        assert not _listening_off_loopback(port)
        # Reopened, it asks for the same port, so a phone's bookmark keeps working.
        assert app.state.device_registry.door_port() == port


# ---- each layer on its own ----------------------------------------------------------------------


def test_the_owner_app_never_takes_a_device_for_the_screen_even_with_its_header(home: Path) -> None:
    """The middleware alone, without the door's header stripping in front of it."""
    app = _app(home)
    screen = app.state.screen_credential
    asked = _Asked(home)
    sent: list[dict[str, Any]] = []
    body = json.dumps({"approved": True}).encode()

    async def call() -> None:
        scope = {
            "type": "http", "method": "POST", "path": f"/api/approvals/{asked.id}", "scheme": "http",
            "query_string": b"", "client": PHONE, "server": ("192.168.1.5", 8123), "root_path": "",
            "http_version": "1.1", "asgi": {"version": "3.0"},
            "headers": [
                (b"host", b"192.168.1.5:8123"), (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (sc.HEADER_NAME.encode(), str(screen.value).encode()),
            ],
            "chimera.via_device": "d1",
        }
        done = False

        async def receive() -> dict[str, Any]:
            nonlocal done
            if not done:
                done = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)

    with TestClient(app) as client:
        client.portal.call(call)  # type: ignore[union-attr]
    answer = json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))
    assert answer["outcome"] == "needs_code" and answer["reason"] == "not_the_screen", answer
    assert pending.is_waiting(home, asked.id)
    pending.answer(home, asked.id, False, via="app")
    asked.outcome()


def test_the_door_strips_the_bearer_the_screen_header_and_the_cookie(home: Path) -> None:
    registry = DeviceRegistry(home)
    seen: list[dict[bytes, bytes]] = []

    async def owner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(dict(scope["headers"]))
        assert scope["chimera.via_device"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    registry.codes.mint()
    _device, cookie = registry.issue(name="phone", address=PHONE[0])
    with TestClient(DeviceGateway(owner, registry), base_url="http://192.168.1.5:8123", client=PHONE) as phone:
        got = phone.get(
            "/api/code/sessions",
            headers={
                "authorization": f"Bearer {TOKEN}", sc.HEADER_NAME: "copied", "cookie": f"{COOKIE_NAME}={cookie}",
            },
        )
    assert got.status_code == 200
    assert not {b"authorization", b"cookie", sc.HEADER_NAME.encode()} & set(seen[0]), seen[0]


def test_a_device_is_answered_under_the_name_it_used_behind_a_loopback_bind(home: Path) -> None:
    """The app's own listener is loopback, whose Host guard refuses a LAN address or a tunnel's name.
    A device request comes in through its own door, not that listener."""
    app = _app(home)
    app.state.bound_address = ("127.0.0.1", 8765)
    with _phone(app) as phone, TestClient(app, base_url="http://192.168.1.5:8765") as stranger:
        _pair(app, phone)
        assert phone.get("/api/code/sessions").status_code == 200
        assert phone.get("/api/code/sessions", headers={"host": "box.tailnet.ts.net"}).status_code == 200
        # The loopback listener itself still refuses a request addressed to a LAN name.
        assert stranger.get("/api/code/sessions", headers=_owner()).status_code == 403


def test_revoke_all_replaces_the_secret_not_only_the_list(home: Path) -> None:
    registry = DeviceRegistry(home)
    device, cookie = registry.issue(name="phone", address="10.0.0.1")
    listed = (home / "devices.json").read_text(encoding="utf-8")
    assert registry.revoke_all() == 1
    # Put the device back in the list, as a restored backup would: its old cookie must still fail.
    (home / "devices.json").write_text(listed, encoding="utf-8")
    fresh = DeviceRegistry(home)
    assert device.id in {d.id for d in fresh.devices()}
    assert fresh.verify(cookie) is None, "revoke all left the signing secret in place"
