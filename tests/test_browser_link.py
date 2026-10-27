from __future__ import annotations

import json
import multiprocessing.connection
import re
import threading
import time
from pathlib import Path

import pytest

from chimera.browser_link import (
    MAX_LINK_MESSAGE_BYTES,
    BrowserLink,
    endpoint,
    load_or_create_pairing_key,
)


def _host(home: Path, handler: object, ready: threading.Event) -> None:
    key = load_or_create_pairing_key(home)
    assert key is not None
    address, family = endpoint(home)
    connection = multiprocessing.connection.Client(address, family=family, authkey=key)
    ready.set()
    try:
        while True:
            try:
                if not connection.poll(1):
                    continue
                request = json.loads(
                    connection.recv_bytes(maxlength=MAX_LINK_MESSAGE_BYTES).decode()
                )
            except (EOFError, OSError, BrokenPipeError):
                return
            if handler == "late":
                connection.send_bytes(json.dumps({"id": -1, "ok": True, "result": []}).encode())
                time.sleep(0.03)
                connection.send_bytes(
                    json.dumps(
                        {
                            "id": request["id"],
                            "ok": True,
                            "result": {
                                "nodes": [
                                    {"role": "button", "name": "Continue", "backendDOMNodeId": 7}
                                ]
                            },
                        }
                    ).encode()
                )
                return
            if handler == "non-dict":
                connection.send_bytes(b"[]")
                return
            if handler == "timeout":
                time.sleep(0.5)
                continue
            if handler == "disconnect":
                return
    finally:
        connection.close()


def _connected_link(
    tmp_path: Path, handler: str, timeout: float = 0.4
) -> tuple[BrowserLink, threading.Thread, threading.Event]:
    assert load_or_create_pairing_key(tmp_path, create=True)
    link = BrowserLink(tmp_path, request_timeout_seconds=timeout)
    link.start()
    ready = threading.Event()
    host = threading.Thread(target=_host, args=(tmp_path, handler, ready), daemon=True)
    host.start()
    assert ready.wait(2)
    deadline = time.monotonic() + 2
    while not link.connected() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert link.connected()
    return link, host, ready


def test_json_transport_discards_wrong_id_and_returns_browser_elements(tmp_path: Path) -> None:
    from chimera.tools.browser_chrome import ChromeExtensionDriver

    link, host, _ready = _connected_link(tmp_path, "late")
    try:
        assert ChromeExtensionDriver(link.transport()).read()[0].name == "Continue"
    finally:
        link.stop()
        host.join(1)


def test_transport_timeout_and_disconnect_are_errors(tmp_path: Path) -> None:
    link, host, _ready = _connected_link(tmp_path, "timeout", timeout=0.08)
    try:
        with pytest.raises(TimeoutError, match="did not respond"):
            link.transport().send({"id": 1, "op": "read"})
    finally:
        link.stop()
        host.join(1)

    other_home = tmp_path / "disconnect"
    link2, host2, _ready2 = _connected_link(other_home, "disconnect", timeout=0.5)
    try:
        with pytest.raises(RuntimeError, match="disconnected"):
            link2.transport().send({"id": 1, "op": "read"})
    finally:
        link2.stop()
        host2.join(1)


def test_start_does_not_create_pairing_key_and_invalid_key_fails_closed(tmp_path: Path) -> None:
    link = BrowserLink(tmp_path)
    link.start()
    assert not link.paired
    assert not link.connected()
    assert not (tmp_path / "browser" / "pairing.key").exists()
    bad_home = tmp_path / "bad"
    key_path = bad_home / "browser" / "pairing.key"
    key_path.parent.mkdir(parents=True)
    key_path.write_bytes(b"short")
    bad_link = BrowserLink(bad_home)
    bad_link.start()
    assert not bad_link.connected()


def test_host_without_key_reports_not_paired(tmp_path: Path) -> None:
    import io
    import struct

    from chimera.browser_host import _forward_host, read_message

    payload = json.dumps({"id": 7}).encode()
    source = io.BytesIO(struct.pack("<I", len(payload)) + payload)
    target = io.BytesIO()
    _forward_host(tmp_path, source, target, retry=False)
    target.seek(0)
    reply = read_message(target)
    assert reply is not None
    assert reply["ok"] is False
    assert reply["error"] == "Chrome host is not paired with a running Chimera desktop app"
    assert reply["id"] == 7
    assert not (tmp_path / "browser" / "pairing.key").exists()


def test_pairing_vault_migrates_legacy_file_and_unpair_removes_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera import config_vault
    from chimera.browser_link import PAIRING_VAULT_NAME, pairing_key_store

    class MemoryKeyring:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, name: str) -> str | None:
            return self.values.get((service, name))

        def set_password(self, service: str, name: str, value: str) -> None:
            self.values[(service, name)] = value

        def delete_password(self, service: str, name: str) -> None:
            del self.values[(service, name)]

    backend = MemoryKeyring()
    monkeypatch.setattr(config_vault, "_keyring", lambda: backend)
    key_path = tmp_path / "browser" / "pairing.key"
    key_path.parent.mkdir(parents=True)
    expected = b"k" * 32
    key_path.write_bytes(expected)

    assert pairing_key_store(tmp_path) == "vault"
    assert not key_path.exists()
    assert load_or_create_pairing_key(tmp_path) == expected
    assert backend.values[(config_vault.SERVICE, PAIRING_VAULT_NAME)] == expected.hex()
    link = BrowserLink(tmp_path)
    link._pair_code = "123456"
    link._pair_expires = time.monotonic() + 120
    assert link.unpair()
    assert not link.pair_with_code("123456")
    assert load_or_create_pairing_key(tmp_path) is None
    assert PAIRING_VAULT_NAME not in config_vault.stored()


def test_pairing_store_falls_back_to_restricted_file_without_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera import config_vault
    from chimera.browser_link import pairing_key_store

    monkeypatch.setattr(config_vault, "_keyring", lambda: None)
    key = load_or_create_pairing_key(tmp_path, create=True)
    assert key is not None and len(key) == 32
    assert pairing_key_store(tmp_path) == "file"
    key_path = tmp_path / "browser" / "pairing.key"
    assert key_path.read_bytes() == key
    if __import__("os").name != "nt":
        assert key_path.stat().st_mode & 0o777 == 0o600


def test_pair_code_valid_wrong_fifth_attempt_and_expiry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera import browser_link

    # These links never open a pipe; that a code needs a listener is its own test below.
    monkeypatch.setattr(BrowserLink, "listening_for_pairing", lambda _self: True)
    link = BrowserLink(tmp_path)
    monkeypatch.setattr(link, "_restart_paired_listener", lambda: None)
    code, lifetime = link.issue_pair_code()
    assert len(code) == 6 and code.isdigit() and lifetime == 120
    assert not link.pair_with_code("wrong!")
    assert link.pair_with_code(code)
    assert link.paired

    wrong_home = tmp_path / "fifth-attempt"
    limited = BrowserLink(wrong_home)
    actual, _ = limited.issue_pair_code()
    wrong = "000000" if actual != "000000" else "000001"
    for _ in range(5):
        assert not limited.pair_with_code(wrong)
    assert limited._pair_code is None
    assert load_or_create_pairing_key(wrong_home) is None

    expired_home = tmp_path / "expired"
    expired = BrowserLink(expired_home)
    expired.issue_pair_code()
    real_monotonic = time.monotonic
    monkeypatch.setattr(browser_link.time, "monotonic", lambda: real_monotonic() + 121)
    assert not expired.pair_with_code("000000")
    assert expired._pair_code is None


def test_pair_route_requires_owner_screen_and_rejects_bridge_and_device() -> None:
    pytest.importorskip("fastapi")
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from chimera.api import screen_credential
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.browser_api import register_browser_api
    from chimera.api.devices import DEVICE_SCOPE_KEY

    class Link:
        paired = False
        issued = 0

        def issue_pair_code(self) -> tuple[str, int]:
            Link.issued += 1
            return "123456", 120

    app = FastAPI()

    @app.middleware("http")
    async def mark_caller(request, call_next):
        caller = request.headers.get("x-caller")
        if caller == "bridge":
            request.scope[VIA_BRIDGE_SCOPE_KEY] = True
        elif caller == "device":
            request.scope[DEVICE_SCOPE_KEY] = "device-id"
        elif caller != "token-only":
            request.scope[screen_credential.SCOPE_KEY] = True
        return await call_next(request)

    register_browser_api(app, Depends(lambda: None), Link())
    client = TestClient(app)
    assert client.post("/api/browser/link/pair").json() == {"code": "123456", "expires_in": 120}
    assert Link.issued == 1
    for caller in ("token-only", "bridge", "device"):
        denied = client.post("/api/browser/link/pair", headers={"x-caller": caller})
        assert denied.status_code == 403
        assert "code" not in denied.text
    assert Link.issued == 1


def test_transport_rejects_non_object_json(tmp_path: Path) -> None:
    link, host, _ready = _connected_link(tmp_path, "non-dict", timeout=0.5)
    try:
        with pytest.raises(RuntimeError, match="invalid reply"):
            link.transport().send({"id": 1, "op": "read"})
    finally:
        link.stop()
        host.join(1)


def test_host_eof_exits_without_reconnecting(tmp_path: Path) -> None:
    import io

    from chimera.browser_host import _forward_host

    assert load_or_create_pairing_key(tmp_path, create=True)
    source = io.BytesIO(b"")
    target = io.BytesIO()
    _forward_host(tmp_path, source, target)
    assert target.getvalue() == b""


def test_host_connection_code_never_uses_pickle_apis() -> None:
    for name in ("browser_link.py", "browser_host.py"):
        source = (Path(__file__).parents[1] / "chimera" / name).read_text(encoding="utf-8")
        assert re.search(r"\.\s*(?:send|recv)\s*\(", source) is None, name
        assert "send_bytes" in source and "recv_bytes" in source


def test_bad_authkey_is_rejected_without_replacing_host(tmp_path: Path) -> None:
    from chimera.browser_link import PAIRING_KEY_BYTES

    assert load_or_create_pairing_key(tmp_path, create=True)
    link = BrowserLink(tmp_path)
    link.start()
    with pytest.raises(multiprocessing.AuthenticationError):
        multiprocessing.connection.Client(*endpoint(tmp_path), authkey=b"x" * PAIRING_KEY_BYTES)
    assert not link.connected()
    link.stop()


def test_windows_key_acl_uses_icacls_without_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from chimera import browser_link

    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(browser_link.os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", run)
    assert load_or_create_pairing_key(tmp_path, create=True)
    assert calls
    assert calls[0][0][0] == "icacls"
    assert calls[0][1].get("check") is False
    assert calls[0][1].get("shell", False) is False


def test_browser_link_status_is_owner_only_and_returns_no_secrets(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from chimera.api import screen_credential
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.browser_api import register_browser_api
    from chimera.api.devices import DEVICE_SCOPE_KEY

    class Link:
        paired = True
        home = tmp_path

        @staticmethod
        def connected() -> bool:
            return False

    app = FastAPI()

    @app.middleware("http")
    async def mark_caller(request, call_next):
        if request.headers.get("x-caller") == "bridge":
            request.scope[VIA_BRIDGE_SCOPE_KEY] = True
        elif request.headers.get("x-caller") == "device":
            request.scope[DEVICE_SCOPE_KEY] = "device-id"
        elif request.headers.get("x-caller") != "token-only":
            # The app's own screen; "token-only" is a local process holding just the server token.
            request.scope[screen_credential.SCOPE_KEY] = True
        return await call_next(request)

    register_browser_api(app, Depends(lambda: None), Link())
    client = TestClient(app)
    response = client.get("/api/browser/link")
    assert response.status_code == 200
    assert response.json() == {"paired": True, "connected": False, "key_store": "file"}
    assert set(response.json()) == {"paired", "connected", "key_store"}
    assert "pairing.key" not in response.text
    assert "secret" not in response.text.lower()
    for caller in ("bridge", "device"):
        denied = client.get("/api/browser/link", headers={"x-caller": caller})
        assert denied.status_code == 403


def test_browser_stop_and_unpair_are_owner_only(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from chimera.api import screen_credential
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.browser_api import register_browser_api
    from chimera.api.devices import DEVICE_SCOPE_KEY

    class Link:
        paired = True

        @staticmethod
        def connected() -> bool:
            return True

        @staticmethod
        def transport() -> object:
            class Transport:
                @staticmethod
                def send(message: object) -> dict[str, bool]:
                    assert isinstance(message, dict)
                    assert message.get("op") == "close"
                    return {"ok": True}

            return Transport()

        @staticmethod
        def stop() -> None:
            return None

        @staticmethod
        def unpair() -> None:
            return None

    app = FastAPI()

    @app.middleware("http")
    async def mark_caller(request, call_next):
        if request.headers.get("x-caller") == "bridge":
            request.scope[VIA_BRIDGE_SCOPE_KEY] = True
        elif request.headers.get("x-caller") == "device":
            request.scope[DEVICE_SCOPE_KEY] = "device-id"
        elif request.headers.get("x-caller") != "token-only":
            # The app's own screen; "token-only" is a local process holding just the server token.
            request.scope[screen_credential.SCOPE_KEY] = True
        return await call_next(request)

    register_browser_api(app, Depends(lambda: None), Link())
    client = TestClient(app)
    for path in ("/api/browser/link/stop", "/api/browser/link/unpair"):
        for caller in ("bridge", "device"):
            response = client.post(path, headers={"x-caller": caller})
            assert response.status_code == 403
            assert "key" not in response.text.lower()
            assert "secret" not in response.text.lower()


def test_browser_trusted_sites_validates_and_is_owner_only(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from chimera import config
    from chimera.api import screen_credential
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.browser_api import register_browser_api
    from chimera.api.devices import DEVICE_SCOPE_KEY

    # By the variable names: the settings read their environment aliases, and a field-name keyword
    # would be dropped silently.
    settings = config.Settings.model_validate(
        {"CHIMERA_HOME": str(tmp_path), "CHIMERA_BROWSER_TRUSTED_SITES": "shop.example"}
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "chimera.api.config_api.patch_config",
        lambda updates: setattr(settings, "browser_trusted_sites", updates["CHIMERA_BROWSER_TRUSTED_SITES"]),
    )

    class Link:
        paired = False

        @staticmethod
        def connected() -> bool:
            return False

    app = FastAPI()

    @app.middleware("http")
    async def mark_caller(request, call_next):
        if request.headers.get("x-caller") == "bridge":
            request.scope[VIA_BRIDGE_SCOPE_KEY] = True
        elif request.headers.get("x-caller") == "device":
            request.scope[DEVICE_SCOPE_KEY] = "device-id"
        elif request.headers.get("x-caller") != "token-only":
            # The app's own screen; "token-only" is a local process holding just the server token.
            request.scope[screen_credential.SCOPE_KEY] = True
        return await call_next(request)

    register_browser_api(app, Depends(lambda: None), Link())
    client = TestClient(app)
    assert client.get("/api/browser/trusted-sites").json() == {"trusted_sites": ["shop.example"]}
    from chimera.api.config_api import _browser_reach_lists
    assert _browser_reach_lists(settings)["trusted_sites"] == ["shop.example"]
    added = client.post("/api/browser/trusted-sites", json={"host": "shop.example"})
    assert added.json() == {"trusted_sites": ["shop.example"]}
    assert client.post("/api/browser/trusted-sites", json={"host": "*.com"}).status_code == 400
    assert client.post("/api/browser/trusted-sites", json={"host": "https://shop.example"}).status_code == 400
    for caller in ("bridge", "device"):
        assert client.get("/api/browser/trusted-sites", headers={"x-caller": caller}).status_code == 403
        assert client.post("/api/browser/trusted-sites", headers={"x-caller": caller}, json={"host": "other.example"}).status_code == 403
    # The server token alone (a file the agent's shell can read) reads the list but cannot change it.
    assert client.get("/api/browser/trusted-sites", headers={"x-caller": "token-only"}).status_code == 200
    only_token = {"x-caller": "token-only"}
    assert client.post("/api/browser/trusted-sites", headers=only_token, json={"host": "evil.example"}).status_code == 403
    assert client.request("DELETE", "/api/browser/trusted-sites", headers=only_token, json={"host": "shop.example"}).status_code == 403
    assert "evil.example" not in client.get("/api/browser/trusted-sites").json()["trusted_sites"]
    # A loopback name or an IP literal is never a trusted site; a saved wildcard can be removed.
    for local in ("localhost", "app.localhost", "127.0.0.1", "10.0.0.5"):
        assert client.post("/api/browser/trusted-sites", json={"host": local}).status_code == 400
    settings.browser_trusted_sites = "shop.example,*.loja.example"
    assert client.request("DELETE", "/api/browser/trusted-sites", json={"host": "*.loja.example"}).json() == {
        "trusted_sites": ["shop.example"]
    }
    # A hand-edited value that does not parse is a 409 with the parser's reason, not a 500.
    settings.browser_trusted_sites = "*.com"
    assert client.get("/api/browser/trusted-sites").status_code == 409
    assert client.post("/api/browser/trusted-sites", json={"host": "ok.example"}).status_code == 409


def test_registry_selects_chrome_only_for_attended_owner_turns(tmp_path: Path) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings

    class Link:
        @staticmethod
        def connected() -> bool:
            return True

        @staticmethod
        def transport() -> object:
            return object()

    settings = Settings(
        CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_BROWSER_GOOGLE="ask"
    )
    sink = object()
    cases = (
        ("owner-screen", "api:turn", False, False, True, True, "chrome"),
        ("bearer-only", "api:turn", False, False, False, True, "playwright"),
        ("guest", "api:turn", True, False, True, True, "playwright"),
        ("bot", "api:turn", False, True, True, True, "playwright"),
        ("bridge", "api:turn", False, False, False, True, "playwright"),
        ("device", "api:turn", False, False, False, True, "playwright"),
        ("subagent", "subagent", False, False, True, True, "playwright"),
        ("no-link", "api:turn", False, False, True, False, "playwright"),
    )
    for label, surface, guest, bot_floor, allowed, has_link, expected in cases:
        ws = tmp_path / label
        ws.mkdir()
        registry, _ledger = assemble_registry(
            CodeSeams(),
            ws,
            settings,
            object(),
            steps=1,
            surface=surface,
            approval_sink=sink,
            guest=guest,
            bot_floor=bot_floor,
            browser_allowed=allowed,
            browser_link=Link() if has_link else None,
        )
        wrapped = registry.get("browser")
        while hasattr(wrapped, "inner"):
            wrapped = wrapped.inner
        assert wrapped._engine == expected, label
        assert ("google_search" in wrapped.parameters["properties"]["action"]["enum"]) == (
            expected == "chrome"
        ), label


def test_register_apply_never_prints_pairing_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys
    import types
    from unittest.mock import patch

    from scripts import register_chrome_host

    class FakeWinreg:
        HKEY_CURRENT_USER = object()
        REG_SZ = 1

        @staticmethod
        def CreateKey(*_args: object) -> object:
            class Key:
                def __enter__(self) -> Key:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

            return Key()

        @staticmethod
        def SetValueEx(*_args: object) -> None:
            return None

    monkeypatch.setattr(register_chrome_host, "_on_windows", lambda: True)
    monkeypatch.setattr(register_chrome_host.Path, "is_file", lambda _self: True)
    # The interpreter here is a fake python.exe; the vault gate has its own tests in test_browser_host.py.
    monkeypatch.setattr(register_chrome_host, "vault_probe", lambda _python: (True, "", ""))
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)
    monkeypatch.setattr("chimera.config.get_settings", lambda: types.SimpleNamespace(home=tmp_path))
    with patch.object(
        sys,
        "argv",
        [
            "register_chrome_host",
            "a" * 32,
            "--apply",
            "--python",
            str(tmp_path / "python.exe"),
            "--directory",
            str(tmp_path / "host"),
        ],
    ):
        assert register_chrome_host.main() == 0
    printed = capsys.readouterr().out
    assert "pairing.key" not in printed
    assert not (tmp_path / "browser" / "pairing.key").exists()
    assert load_or_create_pairing_key(tmp_path) is None
    wrapper = (tmp_path / "host" / "chimera-browser-host.cmd").read_bytes()
    assert wrapper.count(b"\r\n") == 2 and b"\r\r\n" not in wrapper
    assert b"CHIMERA_BROWSER_HOST_EXPECTED_EXE=" in wrapper
