"""A browser tab becomes the owner's screen only with a code the desktop app shows.

The owner's decision of 2026-11-04. What each test would catch if the rule broke:

* a caller other than the desktop's own screen minting a code (bearer-only, the bridge, a device, a
  browser session, or a screen the page itself offers) — the agent could then pair its own browser;
* a right code not giving the tab the screen's switch, or a wrong one giving it;
* a code that survives five wrong guesses, outlives its five minutes, or works twice;
* a revoked session, or an expired one, keeping its screen for one more request;
* a secret or a code reaching a response, a log line, the audit log or the file on disk after creation;
* a refusal that tells a bridge or a device to show a code prompt it could never answer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import screen_credential as sc
from chimera.api.bridge_routes import ROUTES, VIA_BRIDGE_SCOPE_KEY
from chimera.api.browser_screen import (
    BROWSER_SCOPE_KEY,
    COOKIE_NAME,
    DESKTOP_SCREEN_SCOPE_KEY,
    NEEDS_SCREEN_HEADER,
    BrowserScreens,
    desktop_screen_refusal,
    needs_screen,
)
from chimera.api.browser_screen_api import _exchange_refusal
from chimera.api.devices import CODE_SECONDS, DEVICE_SCOPE_KEY
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

TOKEN = "owner-bearer-token-for-browser-pairing-tests-0123"
SCREEN = "desktop-screen-credential-drawn-for-this-test-0123456789"
PAGE_SCREEN = "page-offered-credential-drawn-for-this-test-0123456789"
BEARER = {"authorization": f"Bearer {TOKEN}"}
CODE_SHAPE = re.compile(
    r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$"
)
#: The switch a paired browser must be able to use: an owner-only setting, which needs the screen.
SCREEN_SETTING = {"CHIMERA_PULL_REQUESTS": "false"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `PATCH /api/config` writes `.env` in the working directory. With the screen credential it succeeds,
    # and left in the repository root it wrote `CHIMERA_PULL_REQUESTS=false` there for every later test
    # to read: invisible while that was the default, a failure in two other files once it became `true`.
    monkeypatch.chdir(tmp_path)
    # The app reads the server token from the environment and the settings cache: restore both.
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", TOKEN)
    monkeypatch.setenv("CHIMERA_PULL_REQUESTS", "")
    get_settings.cache_clear()


def _app(home: Path, screen: sc.ScreenCredential) -> Any:
    get_settings.cache_clear()
    settings = Settings(
        CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN
    )
    return build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)),
        workspace=home.parent,
        settings=settings,
        screen=screen,
    )


def _desktop(app: Any) -> TestClient:
    """The desktop app's webview: the bearer token and its own screen credential."""
    return TestClient(app, headers={**BEARER, sc.HEADER_NAME: SCREEN})


def _browser(app: Any) -> TestClient:
    """A browser tab on the service's URL: the bearer token (a loopback page carries it), no screen."""
    # A tab's own requests say they are from this origin, as a browser does.
    return TestClient(app, headers={**BEARER, "sec-fetch-site": "same-origin"})


def _set_clock(app: Any, clock: Any) -> None:
    """Move every time-dependent piece of the registry to ``clock`` (a callable returning seconds)."""
    screens: BrowserScreens = app.state.browser_screens
    screens._clock = clock
    screens.codes._clock = clock


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _mint(app: Any) -> str:
    response = _desktop(app).post("/api/security/browser-screen/code")
    assert response.status_code == 200, response.text
    code = response.json()["code"]
    assert CODE_SHAPE.match(code)
    return code


def _pair(app: Any, browser: TestClient, code: str) -> dict[str, Any]:
    response = browser.post("/api/security/browser-screen/exchange", json={"code": code})
    assert response.status_code == 200, response.text
    return response.json()


def _screen_refused(browser: TestClient) -> bool:
    return browser.patch("/api/config", json=SCREEN_SETTING).status_code == 403


def _secret_from_cookie(client: TestClient) -> str:
    value = client.cookies.get(COOKIE_NAME)
    assert value and "." in value
    return value.partition(".")[2]


# ---- who may mint ----------------------------------------------------------------------------------


def test_only_the_desktop_screen_mints_a_code(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))

    # Bearer token alone: the agent's own shell can read it from the service file.
    bearer_only = TestClient(app, headers=BEARER).post("/api/security/browser-screen/code")
    assert bearer_only.status_code == 403
    assert "desktop" in bearer_only.json()["detail"]
    assert (
        NEEDS_SCREEN_HEADER not in bearer_only.headers
    )  # a bearer caller is not offered the prompt

    # The desktop's own screen mints.
    minted = _desktop(app).post("/api/security/browser-screen/code")
    assert minted.status_code == 200
    assert minted.json()["seconds"] == CODE_SECONDS

    # A screen the PAGE offers (`chimera app` in a browser) is readable by the agent's shell, so it
    # must not mint — the same reason the bypass switch refuses it.
    page_app = _app(tmp_path / "page", sc.ScreenCredential(value=PAGE_SCREEN, in_page=True))
    page = TestClient(page_app, headers={**BEARER, sc.HEADER_NAME: PAGE_SCREEN})
    assert page.post("/api/security/browser-screen/code").status_code == 403

    # No screen at all (the service with no window): nothing to vouch for, so nothing minted.
    bare_app = _app(tmp_path / "bare", sc.ScreenCredential())
    bare = TestClient(bare_app, headers={**BEARER, sc.HEADER_NAME: SCREEN})
    assert bare.post("/api/security/browser-screen/code").status_code == 403


def test_a_paired_browser_cannot_mint_list_or_revoke(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _browser(app)
    _pair(app, browser, _mint(app))

    assert browser.post("/api/security/browser-screen/code").status_code == 403
    assert browser.get("/api/security/browser-screen/sessions").status_code == 403
    session_id = (
        _desktop(app).get("/api/security/browser-screen/sessions").json()["sessions"][0]["id"]
    )
    assert browser.delete(f"/api/security/browser-screen/sessions/{session_id}").status_code == 403
    assert browser.delete("/api/security/browser-screen/sessions").status_code == 403
    # Refused, and still paired: the refusals above did not revoke anything.
    assert not _screen_refused(browser)


def test_the_gate_names_the_bridge_a_device_a_browser_and_a_page_screen() -> None:
    desktop = {DESKTOP_SCREEN_SCOPE_KEY: True}
    assert desktop_screen_refusal(desktop) == ""
    assert "bridge" in desktop_screen_refusal({**desktop, VIA_BRIDGE_SCOPE_KEY: True})
    assert "device" in desktop_screen_refusal({**desktop, DEVICE_SCOPE_KEY: "dev"})
    assert desktop_screen_refusal({**desktop, BROWSER_SCOPE_KEY: "sess"}) != ""
    assert desktop_screen_refusal({DESKTOP_SCREEN_SCOPE_KEY: False}) != ""
    assert desktop_screen_refusal({}) != ""


def test_the_bridge_and_a_device_may_not_exchange_a_code() -> None:
    class _Req:
        def __init__(self, scope: dict[str, Any]) -> None:
            self.scope = scope

    assert "bridge" in _exchange_refusal(_Req({VIA_BRIDGE_SCOPE_KEY: True}))
    assert "device" in _exchange_refusal(_Req({DEVICE_SCOPE_KEY: "dev"}))
    assert _exchange_refusal(_Req({})) == ""


def test_the_bridge_table_names_no_pairing_route() -> None:
    # Claude reaches only what `ROUTES` lists. None of the browser-screen paths may be among them.
    paths = {route.path for route in ROUTES.values()}
    assert not any("browser-screen" in path for path in paths)


def test_a_refusal_offers_the_code_prompt_only_where_a_browser_could_pair() -> None:
    class _Req:
        def __init__(self, scope: dict[str, Any]) -> None:
            self.scope = scope

    browser = needs_screen(_Req({}), "owner screen credential required")
    assert browser.headers == {NEEDS_SCREEN_HEADER: "1"}
    for scope in ({VIA_BRIDGE_SCOPE_KEY: True}, {DEVICE_SCOPE_KEY: "dev"}):
        refused = needs_screen(_Req(scope), "owner screen credential required")
        assert not refused.headers, scope


# ---- the exchange ----------------------------------------------------------------------------------


def test_a_right_code_makes_the_tab_the_screen_for_its_session(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _browser(app)
    code = _mint(app)

    # Before: the owner's bearer alone does not get the screen's switch.
    refused = browser.patch("/api/config", json=SCREEN_SETTING)
    assert refused.status_code == 403
    assert refused.headers.get(NEEDS_SCREEN_HEADER) == "1"  # a browser is offered the prompt

    paired = _pair(app, browser, code)
    assert paired["ok"] is True and paired["name"]

    # The cookie is HttpOnly, SameSite=Strict, and carries no code.
    set_cookie = browser.cookies.get(COOKIE_NAME)
    assert set_cookie and code.replace("-", "") not in set_cookie

    # After: the same request the bearer alone was refused now goes through the screen.
    assert not _screen_refused(browser)


def test_the_cookie_is_httponly_and_strict(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _browser(app)
    response = browser.post("/api/security/browser-screen/exchange", json={"code": _mint(app)})
    header = response.headers.get("set-cookie", "").lower()
    assert f"{COOKIE_NAME}=" in header
    assert "httponly" in header
    assert "samesite=strict" in header


def test_five_wrong_guesses_kill_the_code(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    code = _mint(app)
    wrong = "2222-2222" if code != "2222-2222" else "3333-3333"
    browser = _browser(app)
    for _ in range(5):
        assert (
            browser.post("/api/security/browser-screen/exchange", json={"code": wrong}).status_code
            == 403
        )
    assert app.state.browser_screens.codes.live() is False
    # The right code, now, is refused: the five guesses spent it.
    assert (
        browser.post("/api/security/browser-screen/exchange", json={"code": code}).status_code
        == 403
    )
    assert COOKIE_NAME not in browser.cookies


def test_an_expired_code_is_refused(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    clock = _Clock(1_000_000.0)
    _set_clock(app, clock)
    code = _mint(app)
    clock.now += CODE_SECONDS + 1
    browser = _browser(app)
    assert (
        browser.post("/api/security/browser-screen/exchange", json={"code": code}).status_code
        == 403
    )
    assert COOKIE_NAME not in browser.cookies


def test_a_used_code_is_refused(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    code = _mint(app)
    browser = _browser(app)
    _pair(app, browser, code)
    other = _browser(app)
    again = other.post("/api/security/browser-screen/exchange", json={"code": code})
    assert again.status_code == 403
    assert COOKIE_NAME not in other.cookies


# ---- revoking and expiry ---------------------------------------------------------------------------


def test_revoke_takes_the_screen_away_at_once(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _browser(app)
    _pair(app, browser, _mint(app))
    assert not _screen_refused(browser)

    listed = _desktop(app).get("/api/security/browser-screen/sessions").json()
    assert len(listed["sessions"]) == 1
    session = listed["sessions"][0]
    assert "secret" not in json.dumps(session) and "sha256" not in json.dumps(session)

    revoked = _desktop(app).delete(f"/api/security/browser-screen/sessions/{session['id']}")
    assert revoked.json()["revoked"] == 1
    # The very next request from that tab: no screen, and the prompt is offered again.
    after = browser.patch("/api/config", json=SCREEN_SETTING)
    assert after.status_code == 403
    assert after.headers.get(NEEDS_SCREEN_HEADER) == "1"


def test_revoke_all_clears_every_paired_browser(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    first, second = _browser(app), _browser(app)
    _pair(app, first, _mint(app))
    _pair(app, second, _mint(app))
    assert len(_desktop(app).get("/api/security/browser-screen/sessions").json()["sessions"]) == 2

    cleared = _desktop(app).delete("/api/security/browser-screen/sessions").json()
    assert cleared["revoked"] == 2
    assert _screen_refused(first) and _screen_refused(second)


def test_a_session_ends_when_its_time_runs_out(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    clock = _Clock(2_000_000.0)
    _set_clock(app, clock)
    browser = _browser(app)
    _pair(app, browser, _mint(app))
    assert not _screen_refused(browser)

    clock.now += app.state.browser_screens.seconds + 1
    assert _screen_refused(browser)


# ---- what is never written down ----------------------------------------------------------------------


def test_nothing_secret_is_logged_or_kept_in_the_clear(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    home = tmp_path / "home"
    app = _app(home, sc.ScreenCredential(value=SCREEN))
    code = _mint(app)
    bare_code = code.replace("-", "")
    browser = _browser(app)
    exchanged = browser.post("/api/security/browser-screen/exchange", json={"code": code})
    secret = _secret_from_cookie(browser)
    listed = _desktop(app).get("/api/security/browser-screen/sessions").text

    # The code and the secret: in no response after the one that made them, in no log line.
    for body in (exchanged.text, listed):
        assert bare_code not in body and code not in body
        assert secret not in body
    assert bare_code not in caplog.text and code not in caplog.text
    assert secret not in caplog.text

    # On disk: the audit log and the session file. The session file holds a digest, never the secret.
    audit_text = (home / "audit.jsonl").read_text(encoding="utf-8")
    sessions_text = (home / "browser-screen.json").read_text(encoding="utf-8")
    for text in (audit_text, sessions_text):
        assert code not in text and bare_code not in text
        assert secret not in text
    assert '"digest"' in sessions_text

    # Every mint, exchange and revoke is in the audit log, with ids only.
    _desktop(app).delete("/api/security/browser-screen/sessions")
    entries = [
        json.loads(line) for line in (home / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    actions = [entry["action"] for entry in entries if entry.get("type") == "browser_screen"]
    assert actions[:2] == ["mint", "exchange"]
    assert "revoke_all" in actions


def test_a_refused_guess_is_audited_without_the_guess(tmp_path: Path) -> None:
    home = tmp_path / "home"
    app = _app(home, sc.ScreenCredential(value=SCREEN))
    _mint(app)
    _browser(app).post("/api/security/browser-screen/exchange", json={"code": "ZZZZ-ZZZZ"})
    text = (home / "audit.jsonl").read_text(encoding="utf-8")
    assert "exchange_refused" in text
    assert "ZZZZ" not in text


# ---- review fixes: origin, shared store, forged entries, scope, limiter ------------------------------


def _paired(app: Any) -> TestClient:
    browser = _browser(app)
    _pair(app, browser, _mint(app))
    assert not _screen_refused(browser)
    return browser


def test_a_cookie_does_not_ride_a_cross_origin_write(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _paired(app)
    write = {"json": SCREEN_SETTING}
    # Another localhost port: its Origin differs from this server's own origin.
    cross_port = browser.patch(
        "/api/config", headers={"sec-fetch-site": "", "origin": "http://testserver:9999"}, **write
    )
    assert cross_port.status_code == 403
    # A cross-site page, which the browser labels so.
    cross_site = browser.patch("/api/config", headers={"sec-fetch-site": "cross-site"}, **write)
    assert cross_site.status_code == 403
    # Neither header at all: a script, not a tab of this app.
    missing = browser.patch("/api/config", headers={"sec-fetch-site": ""}, **write)
    assert missing.status_code == 403
    # The session itself is untouched: this origin still has the screen.
    assert not _screen_refused(browser)


def test_this_servers_own_origin_may_write_with_the_cookie(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _paired(app)
    by_origin = browser.patch(
        "/api/config",
        headers={"sec-fetch-site": "", "origin": "http://testserver"},
        json=SCREEN_SETTING,
    )
    assert by_origin.status_code != 403


def test_two_processes_on_one_home_share_a_revocation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first, second = BrowserScreens(home), BrowserScreens(home)
    session, cookie = first.exchange(first.mint()[0], user_agent="", address="")
    assert first.verify(cookie) is not None

    # The other process revokes it: the first process's next check refuses it.
    assert second.revoke(session.id)
    assert first.verify(cookie) is None

    # A later write by the first process does not bring the revoked session back.
    first.exchange(first.mint()[0], user_agent="", address="")
    listed = {s.id for s in first.sessions()}
    assert session.id not in listed
    assert session.id not in (home / "browser-screen.json").read_text(encoding="utf-8")


def test_an_entry_written_by_hand_is_not_accepted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    screens = BrowserScreens(home)
    secret = "a-secret-that-someone-wrote-into-the-file-by-hand-0123"
    session_id = "abcd1234abcd1234"
    now = time.time()
    forged = {
        "sessions": [
            {
                "id": session_id,
                "name": "forged",
                "digest": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                "created_at": now,
                "last_used_at": now,
                "expires_at": now + 3600,
                "last_ip": "",
            }
        ]
    }
    (home / "browser-screen.json").write_text(json.dumps(forged), encoding="utf-8")
    assert screens.verify(f"{session_id}.{secret}") is None
    # A new process, with its own key, does not accept it either.
    assert BrowserScreens(home).verify(f"{session_id}.{secret}") is None


def test_a_browser_cannot_turn_the_bridge_or_its_full_control_on(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _paired(app)
    for name in ("CHIMERA_DESKTOP_BRIDGE", "CHIMERA_DESKTOP_BRIDGE_FULL"):
        refused = browser.patch("/api/config", json={name: "true"})
        assert refused.status_code == 403, name
        assert "desktop app's screen" in refused.json()["detail"]
    full = browser.post("/api/security/bridge-full-control", json={"on": True})
    assert full.status_code == 403
    assert "desktop app's screen" in full.json()["detail"]
    # Turning them OFF only narrows, and a browser may do it.
    off = browser.patch("/api/config", json={"CHIMERA_DESKTOP_BRIDGE": "false"})
    assert off.status_code != 403


def test_a_browser_cannot_turn_ignore_permissions_on(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    browser = _paired(app)
    toggled = browser.patch(
        "/api/code/sessions/no-such-session/permissions", json={"bypass_permissions": True}
    )
    assert toggled.status_code == 403
    assert "desktop app" in toggled.json()["detail"]


def test_a_local_process_cannot_lock_the_owner_out_of_pairing(tmp_path: Path) -> None:
    app = _app(tmp_path / "home", sc.ScreenCredential(value=SCREEN))
    _mint(app)
    # A process on the same address burns the live code's guesses. There is no per-address bucket,
    # so the only thing it reaches is that one code.
    attacker = _browser(app)
    for _ in range(5):
        assert (
            attacker.post(
                "/api/security/browser-screen/exchange", json={"code": "2222-2222"}
            ).status_code
            == 403
        )
    # The owner mints again from the desktop and pairs at once, from the same address.
    _pair(app, _browser(app), _mint(app))


def test_refused_exchanges_are_audited_once_per_code_and_logged_a_minute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    home = tmp_path / "home"
    clock = _Clock(3_000_000.0)
    screens = BrowserScreens(home, clock=clock, seconds=3600)
    screens.mint()
    # A process guessing in a loop: twenty refusals against the one code.
    for _ in range(20):
        assert screens.exchange("2222-2222", user_agent="", address="") is None
    audit = [
        json.loads(line) for line in (home / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    refused = [
        e
        for e in audit
        if e.get("type") == "browser_screen" and e.get("action") == "exchange_refused"
    ]
    assert len(refused) == 1
    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(lines) == 1 and lines[0].startswith("1 browser pairing")

    # A minute later the next refusal logs again, with the count that accumulated in between.
    clock.now += 61
    assert screens.exchange("2222-2222", user_agent="", address="") is None
    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(lines) == 2 and lines[1].startswith("20 browser pairing")
    audit = [
        json.loads(line) for line in (home / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len([e for e in audit if e.get("action") == "exchange_refused"]) == 1
