"""Study 29, P5.2 review: a declared local port is reached by the agent, and by the dev app's own pages.

`CHIMERA_BROWSER_LOCAL_PORTS` exists so the agent can look at the dev server it is changing. The
first version opened the declared port to every request the browser made, from any page: a site
the agent visited could send the dev server an ``<img>``, a ``no-cors`` POST, a form POST or a
``location=`` — reproduced on a real Chromium by the review, the dev server logged all three. The
guard judged the target and never who sent it.

Now a request to a declared port goes only when the page sending it is itself on a declared port,
or when it is the agent's own top-level navigation (and that navigation's redirect hops). The unit
tests drive the guard with the events Chromium sends; the browser tests use a real Chromium (skipped
where it cannot launch), where a server on ``127.0.0.1`` plays the public web (the floor is injected
to allow exactly that port).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import pytest

from chimera.tools.browser_playwright import PlaywrightDriver, RequestGuard
from chimera.tools.browser_reach import OTHER, BrowserReach

DEV = "http://localhost:3000"
API = "http://localhost:3001"  # a second declared port, the dev app's own backend
PUBLIC = "https://example.com"
MAIN = "MAIN"


def _guard() -> RequestGuard:
    reach = BrowserReach(
        local_ports={3000, 3001},
        owned=frozenset,
        floor=lambda url: urlparse(url).hostname == "example.com",
        answers=lambda url: OTHER,
    )
    return RequestGuard(reach.permits, cache=False, local=reach.declared_local)


class _Session:
    def __init__(self) -> None:
        self.verdicts: dict[str, str] = {}

    def send(self, method: str, params: dict[str, Any]) -> None:
        self.verdicts[str(params["requestId"])] = "go" if method == "Fetch.continueRequest" else "no"


def _ask(guard: RequestGuard, session: _Session, rid: str, url: str, kind: str, frame: str = MAIN, net: str = "") -> str:
    guard.on_paused(
        session,
        {"requestId": rid, "request": {"url": url}, "resourceType": kind, "frameId": frame, "networkId": net or rid},
        main_frame=MAIN,
    )
    return session.verdicts[rid]


def _on(guard: RequestGuard, url: str, frame: str = MAIN, parent: str | None = None) -> None:
    guard.frame_navigated({"frame": {"id": frame, "url": url, **({"parentId": parent} if parent else {})}})


def test_a_public_page_cannot_send_anything_to_a_declared_port() -> None:
    guard, session = _guard(), _Session()
    _on(guard, f"{PUBLIC}/")
    assert _ask(guard, session, "img", f"{DEV}/approve.png", "Image") == "no"
    assert _ask(guard, session, "post", f"{DEV}/api/approve", "Fetch") == "no"
    assert _ask(guard, session, "form", f"{DEV}/api/approve", "Document") == "no"  # form / location=
    assert _ask(guard, session, "own", f"{PUBLIC}/next", "Document") == "go"  # its own web still works
    assert guard.refusal(f"{DEV}/api/approve").startswith("blocked navigation to a declared local port")


def test_the_agents_own_navigation_reaches_the_dev_server_and_its_hops_follow() -> None:
    guard, session = _guard(), _Session()
    _on(guard, f"{PUBLIC}/")
    with guard.expecting(f"{DEV}/", MAIN):
        assert _ask(guard, session, "nav", f"{DEV}/", "Document", net="n1") == "go"
        # The dev server redirects its own navigation to the other declared port: same request.
        assert _ask(guard, session, "hop", f"{API}/login", "Document", net="n1") == "go"
    # After the navigation, nothing is expected any more.
    assert _ask(guard, session, "late", f"{DEV}/", "Document", net="n2") == "no"


def test_a_navigation_the_agent_aimed_at_a_public_site_cannot_be_redirected_to_the_dev_server() -> None:
    """Even while the page being left is the dev app itself: it is not the one asking."""
    guard, session = _guard(), _Session()
    _on(guard, f"{DEV}/")
    with guard.expecting(f"{PUBLIC}/promo", MAIN):
        assert _ask(guard, session, "pub", f"{PUBLIC}/promo", "Document", net="n1") == "go"
        assert _ask(guard, session, "hop", f"{DEV}/api/reset", "Document", net="n1") == "no"


def test_the_dev_apps_own_pages_reach_both_declared_ports() -> None:
    guard, session = _guard(), _Session()
    _on(guard, f"{DEV}/")
    assert _ask(guard, session, "xhr", f"{API}/items", "XHR") == "go"
    assert _ask(guard, session, "js", f"{DEV}/src/main.tsx", "Script") == "go"
    assert _ask(guard, session, "link", f"{DEV}/settings", "Document") == "go"  # a click inside it
    # A frame inside the dev page, and a srcdoc frame that is the page's own origin.
    guard.frame_attached({"frameId": "F1", "parentFrameId": MAIN})
    assert _ask(guard, session, "frame", f"{DEV}/embed", "Document", frame="F1") == "go"
    _on(guard, "about:srcdoc", frame="F2", parent=MAIN)
    assert _ask(guard, session, "srcdoc", f"{API}/x", "Fetch", frame="F2") == "go"


def test_a_frame_in_a_public_page_cannot_reach_the_dev_server() -> None:
    guard, session = _guard(), _Session()
    _on(guard, f"{PUBLIC}/")
    guard.frame_attached({"frameId": "F1", "parentFrameId": MAIN})
    assert _ask(guard, session, "frame", f"{DEV}/", "Document", frame="F1") == "no"
    _on(guard, "about:blank", frame="F2", parent=MAIN)
    assert _ask(guard, session, "blank", f"{DEV}/api", "Fetch", frame="F2") == "no"
    assert _ask(guard, session, "unseen", f"{DEV}/api", "Fetch", frame="NEVER-SEEN") == "no"


def test_back_is_the_agents_own_navigation() -> None:
    guard, session = _guard(), _Session()
    _on(guard, f"{PUBLIC}/")
    with guard.expecting("*", MAIN):
        assert _ask(guard, session, "back", f"{DEV}/", "Document") == "go"


# --- a real browser --------------------------------------------------------------------------


class _Server(BaseHTTPRequestHandler):
    """Records every GET and POST it serves; the dev server stand-in must record none from a public page."""

    def log_message(self, *args: object) -> None:
        return

    def _serve(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.hits.append(f"{method} {self.path}")  # type: ignore[attr-defined]
        hop = getattr(self.server, "redirects", {}).get(self.path)
        if hop:
            self.send_response(302)
            self.send_header("Location", hop)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        pages: dict[str, str] = self.server.pages  # type: ignore[attr-defined]
        data = pages.get(self.path.split("?")[0], "<p>ok</p>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")


def _start(pages: dict[str, str]) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    server.hits = []  # type: ignore[attr-defined]
    server.pages = pages  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


@pytest.fixture()
def world() -> Iterator[dict[str, Any]]:
    dev, dev_port = _start({})
    api, api_port = _start({"/login": "<p>api-login</p>"})
    public, public_port = _start({})
    target = f"http://localhost:{dev_port}/api/approvals/1"
    dev.pages.update({  # type: ignore[attr-defined]
        "/": "<p>dev-home</p><script>fetch('/api/items')</script>",
    })
    dev.redirects = {"/hop": f"http://localhost:{api_port}/login"}  # type: ignore[attr-defined]
    public.pages.update({  # type: ignore[attr-defined]
        "/sub": (
            f"<p>public-sub</p><img src='{target}?img'>"
            f"<script>fetch('{target}?fetch', {{method: 'POST', mode: 'no-cors', body: 'approve'}})</script>"
        ),
        "/form": (
            f"<form method='post' action='{target}?form'><input name='decision' value='approve'></form>"
            "<script>document.forms[0].submit()</script>"
        ),
        "/location": f"<p>public-location</p><script>location = '{target}?location'</script>",
        "/link": f"<a href='{target}?click'>approve</a>",
    })
    yield {"dev": dev, "dev_port": dev_port, "api": api, "api_port": api_port, "public_port": public_port}
    for server in (dev, api, public):
        server.shutdown()
        server.server_close()


@pytest.fixture()
def driver(world: dict[str, Any]) -> Iterator[PlaywrightDriver]:
    public_port = world["public_port"]
    reach = BrowserReach(
        local_ports={world["dev_port"], world["api_port"]},
        owned=frozenset,
        floor=lambda url: urlparse(url).hostname == "127.0.0.1" and urlparse(url).port == public_port,
    )
    try:
        d = PlaywrightDriver(headless=True, reach=reach)
    except Exception as exc:  # noqa: BLE001 — no Chromium here (CI): these tests need a real browser
        pytest.skip(f"chromium unavailable: {exc}")
    yield d
    d.close()


def test_a_real_public_page_sends_the_dev_server_nothing(world: dict[str, Any], driver: PlaywrightDriver) -> None:
    """The review's probe, on a real Chromium: an image, a no-cors POST, a form POST and a script
    navigation from a page off the loopback. Before the fix the dev server logged them."""
    public = f"http://127.0.0.1:{world['public_port']}"
    for path in ("/sub", "/form", "/location"):
        with contextlib.suppress(ValueError):  # a refused navigation inside `navigate` raises
            driver.navigate(f"{public}{path}")
        driver._page.wait_for_timeout(500)
    elements = driver.navigate(f"{public}/link")
    with pytest.raises(ValueError, match="from a page that is not on one"):
        driver.click(elements[0].ref)
    assert world["dev"].hits == []
    assert driver.guard.foreign, "nothing was refused for its sender"


def test_a_real_agent_still_opens_the_dev_server_and_its_page_reaches_its_own_api(
    world: dict[str, Any], driver: PlaywrightDriver
) -> None:
    driver.navigate(f"http://127.0.0.1:{world['public_port']}/sub")  # coming from a public page
    driver.navigate(f"http://localhost:{world['dev_port']}/")
    driver._page.wait_for_timeout(500)
    assert "dev-home" in driver.page_text()
    assert world["dev"].hits == ["GET /", "GET /api/items"]


def test_a_real_redirect_of_the_agents_own_navigation_follows_to_another_declared_port(
    world: dict[str, Any], driver: PlaywrightDriver
) -> None:
    """The hop is judged by the request it belongs to (Chromium keeps one network id across a
    redirect chain), not by the page being left, which is still the blank page here."""
    driver.navigate(f"http://localhost:{world['dev_port']}/hop")
    assert "api-login" in driver.page_text()
    assert world["api"].hits == ["GET /login"]
