"""Study 24, items 1–2: the browser re-checks every hop, refs are strict, a password is never a name.

``scrape(render="browser")`` opened any URL (only the HTTP path had ``check_url``), and the browser
tool checked the first URL and then let Chromium follow redirects, clicks and script navigation.
The unit tests below run everywhere; the ones taking the ``driver`` fixture drive a real browser
against a local server and skip where Chromium cannot launch (CI has no browser binary).

In the real-browser tests the host ``127.0.0.1`` plays the public web and ``localhost`` plays the
internal address: the same server answers both, and the guard is given ``allowed`` = "host is
127.0.0.1", so what is exercised is the INTERCEPTION — that every request, redirect hops included,
reaches the guard — not DNS.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import pytest

from chimera.scrape import fetch as fetch_mod
from chimera.tools import browser as browser_mod
from chimera.tools.browser_playwright import PlaywrightDriver, RequestGuard
from chimera.tools.scrape import ScrapeTool

# --- the guard, without a browser ----------------------------------------------------------------


class _Session:
    """Records the DevTools commands the guard sends for each paused request."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send(self, method: str, params: dict[str, Any]) -> None:
        self.sent.append((method, params))


def _paused(url: str, kind: str = "Document", request_id: str = "r1") -> dict[str, Any]:
    return {"requestId": request_id, "request": {"url": url}, "resourceType": kind}


def test_the_guard_refuses_an_internal_navigation_and_remembers_it() -> None:
    guard = RequestGuard(lambda url: urlparse(url).hostname == "example.com")
    session = _Session()
    guard.on_paused(session, _paused("https://example.com/", request_id="ok"))
    guard.on_paused(session, _paused("http://169.254.169.254/latest/meta-data", request_id="bad"))
    assert session.sent == [
        ("Fetch.continueRequest", {"requestId": "ok"}),
        ("Fetch.failRequest", {"requestId": "bad", "errorReason": "BlockedByClient"}),
    ]
    assert guard.take() == ["http://169.254.169.254/latest/meta-data"]
    assert guard.take() == []  # taken once


def test_a_refused_subresource_is_dropped_without_failing_the_navigation() -> None:
    guard = RequestGuard(lambda url: False)
    session = _Session()
    guard.on_paused(session, _paused("http://127.0.0.1:8765/api/shutdown", kind="Image"))
    assert session.sent[0][0] == "Fetch.failRequest"
    assert guard.blocked_requests == 1 and guard.take() == []


def test_a_guard_that_cannot_decide_fails_the_request_closed() -> None:
    def broken(url: str) -> bool:
        raise RuntimeError("resolver exploded")

    session = _Session()
    RequestGuard(broken).on_paused(session, _paused("https://example.com/"))
    assert session.sent == [("Fetch.failRequest", {"requestId": "r1", "errorReason": "BlockedByClient"})]


def test_the_guard_resolves_each_host_once_and_ignores_non_http() -> None:
    asked: list[str] = []

    def allowed(url: str) -> bool:
        asked.append(url)
        return True

    guard = RequestGuard(allowed)
    for path in ("/a", "/b", "/c.png"):
        guard.on_paused(_Session(), _paused(f"https://cdn.example.com{path}", kind="Image"))
    assert guard.permits("data:text/plain,hi") and guard.permits("blob:https://x/1")
    assert len(asked) == 1


def test_production_guard_uses_the_ssrf_check() -> None:
    guard = RequestGuard()
    assert not guard.permits("http://169.254.169.254/latest/meta-data")
    assert not guard.permits("http://127.0.0.1:8765/")
    assert not guard.permits("http://[::1]/")


def test_a_driver_action_that_was_refused_is_an_error_not_a_page() -> None:
    driver = object.__new__(PlaywrightDriver)
    driver.guard = RequestGuard(lambda url: False)
    driver._page = SimpleNamespace(evaluate=lambda script: [], goto=lambda *a, **k: None)

    def step() -> None:
        driver.guard.on_paused(_Session(), _paused("http://10.0.0.1/admin"))

    with pytest.raises(ValueError, match="blocked navigation"):
        driver._guarded(step)


def test_a_driver_whose_browser_cannot_launch_stops_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Chromium: the launch fails and the tool builds a second driver after installing. The first
    one's Playwright was never stopped, and its event loop broke 27 later async tests in the gate."""
    import playwright.sync_api as sync_api

    stopped: list[bool] = []

    class _Chromium:
        def launch(self, headless: bool) -> Any:
            raise RuntimeError("Executable doesn't exist — run `playwright install`")

    class _Playwright:
        chromium = _Chromium()

        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: SimpleNamespace(start=lambda: _Playwright()))
    with pytest.raises(RuntimeError, match="playwright install"):
        PlaywrightDriver(headless=True)
    assert stopped == [True]


# --- scrape(render="browser") --------------------------------------------------------------------


def test_scrape_with_the_browser_refuses_an_internal_url_before_launching(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_browser(headless: bool) -> Any:
        raise AssertionError("a browser was launched for an internal URL")

    monkeypatch.setattr(browser_mod, "_new_playwright_driver", no_browser)
    with pytest.raises(ValueError, match="blocked internal address"):
        fetch_mod._browser_fetch("http://169.254.169.254/latest/meta-data")
    out = ScrapeTool().run(url="http://127.0.0.1:8765/api/config", render="browser")
    assert out.startswith("error:") and "blocked internal address" in out


# --- a real browser ------------------------------------------------------------------------------


class _Site(BaseHTTPRequestHandler):
    port = 0

    def log_message(self, *args: object) -> None:  # quiet
        return

    def _send(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        internal = f"http://localhost:{self.port}/internal"
        pages = {
            "/redirect": (302, "", {"Location": internal}),
            "/link": (200, f'<a href="{internal}">go</a>', None),
            "/script": (200, f"<p>hi</p><script>location.href = {internal!r}</script>", None),
            "/image": (200, f'<p>hello</p><img src="{internal}.png">', None),
            "/buttons": (200, "<button>one</button><button>two</button>", None),
            "/login": (200, '<label for="p">Senha</label><input id="p" type="password"><input type="submit" value="Entrar">', None),
            "/internal": (200, "<p>SECRET-METADATA</p>", None),
        }
        status, body, headers = pages.get(self.path.split("?")[0], (404, "no", None))
        self._send(status, body, headers)


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    _Site.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{_Site.port}"
    server.shutdown()


@pytest.fixture()
def driver() -> Iterator[PlaywrightDriver]:
    try:
        d = PlaywrightDriver(headless=True, allowed=lambda url: urlparse(url).hostname == "127.0.0.1")
    except Exception as exc:  # noqa: BLE001 — no Chromium here (CI): these tests need a real browser
        pytest.skip(f"chromium unavailable: {exc}")
    yield d
    d.close()


def test_a_redirect_to_an_internal_address_is_refused(site: str, driver: PlaywrightDriver) -> None:
    with pytest.raises(ValueError, match="blocked navigation"):
        driver.navigate(f"{site}/redirect")
    assert "SECRET-METADATA" not in driver.page_html()


def test_a_click_to_an_internal_address_is_refused(site: str, driver: PlaywrightDriver) -> None:
    elements = driver.navigate(f"{site}/link")
    with pytest.raises(ValueError, match="blocked navigation"):
        driver.click(elements[0].ref)
    assert "SECRET-METADATA" not in driver.page_html()


def test_a_script_navigation_to_an_internal_address_never_loads(site: str, driver: PlaywrightDriver) -> None:
    with contextlib.suppress(ValueError):  # refused during the goto: equally fine
        driver.navigate(f"{site}/script")
    driver._page.wait_for_timeout(500)
    assert driver.guard.blocked_requests >= 1
    assert "SECRET-METADATA" not in driver.page_html()


def test_an_internal_subresource_is_dropped_and_the_page_still_loads(site: str, driver: PlaywrightDriver) -> None:
    driver.navigate(f"{site}/image")
    assert "hello" in driver.page_text()
    assert driver.guard.blocked_requests >= 1


def test_refs_are_cleared_and_a_duplicate_is_refused_not_clicked(site: str, driver: PlaywrightDriver) -> None:
    driver.navigate(f"{site}/buttons")
    # The first button disappears; the next snapshot re-numbers what is left and must not leave
    # the hidden one holding its old ref (which the second button now also gets).
    driver._page.evaluate("document.querySelectorAll('button')[0].style.display = 'none'")
    driver.read()
    assert driver._page.locator("[data-chimera-ref]").count() == 1
    driver._page.evaluate("document.querySelectorAll('button')[0].style.display = ''")
    driver.read()
    assert driver._page.locator("[data-chimera-ref]").count() == 2
    # The page re-stamps behind our back: two elements now claim e1.
    driver._page.evaluate("document.querySelectorAll('button')[1].setAttribute('data-chimera-ref', 'e1')")
    with pytest.raises(ValueError, match="matches 2 elements"):
        driver.click("e1")
    with pytest.raises(KeyError):
        driver.click("e9")


def test_a_typed_password_never_becomes_an_element_name(site: str, driver: PlaywrightDriver) -> None:
    elements = driver.navigate(f"{site}/login")
    field = next(e for e in elements if e.role == "textbox (password)")
    after = driver.type_text(field.ref, "hunter2-s3cret")
    assert all("hunter2" not in e.name for e in after)
    assert any(e.role == "button" and e.name == "Entrar" for e in after)  # a submit's value is still its label
