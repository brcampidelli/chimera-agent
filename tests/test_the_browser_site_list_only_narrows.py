"""Study 29, P5.2(a): the browser's site list asks before a top-level page off it, and never widens.

`CHIMERA_BROWSER_SITES` names the hosts the agent may open without asking (``example.com``, and
``*.example.com`` for its subdomains). A top-level navigation anywhere else is a question for the
person — the same approver that lets a file tool outside the project folder — and a refusal where
nobody can be asked. The list judges the pages the agent VISITS, not what a listed page loads: a
frame or an image from another host is still judged only by the SSRF floor, so a site with a CDN
keeps working. And the list never opens anything: a listed host the floor refuses stays refused.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import pytest

from chimera.tools.browser import BrowserTool, Element
from chimera.tools.browser_playwright import PlaywrightDriver, RequestGuard
from chimera.tools.browser_reach import BrowserReach, parse_sites
from chimera.tools.workspace import BoundaryQuestion


def _public(url: str) -> bool:
    return urlparse(url).hostname in {"example.com", "docs.example.com", "github.com", "evil.example"}


def _reach(*sites: str) -> BrowserReach:
    return BrowserReach(sites or ("example.com", "*.github.com"), floor=_public, owned=frozenset)


# --- the list ---------------------------------------------------------------------------------------


def test_the_list_takes_hosts_and_wildcards_and_refuses_anything_else() -> None:
    assert parse_sites("Example.com, *.github.com  docs.python.org.") == (
        "example.com", "*.github.com", "docs.python.org",
    )
    assert parse_sites("") == ()
    for bad in ("https://example.com", "example.com/path", "example.com:443", "*", "*example.com", "a..b", "-x.com"):
        with pytest.raises(ValueError):
            parse_sites(bad)


def test_a_listed_host_needs_no_question_and_a_wildcard_means_subdomains() -> None:
    reach = _reach()
    assert reach.listed("https://example.com/a")
    assert reach.listed("https://EXAMPLE.com./a")
    assert reach.listed("https://api.github.com/repos")
    assert reach.listed("https://a.b.github.com/")
    assert not reach.listed("https://github.com/")  # the wildcard is the subdomains; list the apex too
    assert not reach.listed("https://notgithub.com/")
    assert not reach.listed("https://example.com.evil.example/")
    assert not reach.listed("https://docs.example.com/")
    assert reach.listed("about:blank") and reach.listed("data:text/html,hi")


def test_no_list_lists_everything() -> None:
    assert BrowserReach(floor=_public).listed("https://evil.example/")


def test_a_declared_local_port_counts_as_listed() -> None:
    reach = BrowserReach(("example.com",), {3000}, owned=lambda: frozenset())
    assert reach.listed("http://localhost:3000/")
    assert not reach.listed("http://localhost:3001/")


def test_the_list_never_opens_what_the_floor_refuses() -> None:
    reach = BrowserReach(("localhost", "127.0.0.1", "169.254.169.254", "10.0.0.5"), owned=frozenset)
    for url in ("http://localhost:3000/", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/"):
        assert reach.listed(url)  # on the list...
        assert not reach.permits(url), url  # ...and still refused
        with pytest.raises(ValueError):
            reach.check(url)


# --- the tool --------------------------------------------------------------------------------------


class _Driver:
    def __init__(self) -> None:
        self.visited: list[str] = []

    def navigate(self, url: str) -> list[Element]:
        self.visited.append(url)
        return [Element("e1", "link", "home")]

    def read(self) -> list[Element]:
        return []

    def page_html(self) -> str:
        return "<p>x</p>"

    def page_text(self) -> str:
        return "x"

    def screenshot(self, path: str) -> None:
        return None

    def frame(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_off_the_list_with_nobody_to_ask_is_a_refusal_before_the_driver() -> None:
    driver = _Driver()
    tool = BrowserTool(driver=driver, reach=_reach())  # type: ignore[arg-type]
    for action in ("navigate", "read_text", "find"):
        out = tool.run(action=action, url="https://evil.example/", query="x")
        assert out.startswith("error:") and "site list" in out and "nobody here can approve" in out, out
    assert driver.visited == []
    assert not tool.run(action="navigate", url="https://example.com/").startswith("error:")
    assert driver.visited == ["https://example.com/"]


def test_off_the_list_asks_the_person_and_a_yes_holds_for_that_host() -> None:
    asked: list[BoundaryQuestion] = []
    driver = _Driver()
    tool = BrowserTool(driver=driver, reach=_reach())  # type: ignore[arg-type]
    tool.ask_outside = lambda question: asked.append(question) or True  # type: ignore[attr-defined]

    assert not tool.run(action="navigate", url="https://evil.example/one").startswith("error:")
    assert not tool.run(action="navigate", url="https://evil.example/two").startswith("error:")
    assert len(asked) == 1  # one question per host, not per page
    assert "evil.example" in asked[0].reason and asked[0].decision == "review"
    assert asked[0].action == "browser: https://evil.example/one"
    assert driver.visited == ["https://evil.example/one", "https://evil.example/two"]


def test_a_no_from_the_person_is_a_refusal_that_says_so() -> None:
    driver = _Driver()
    tool = BrowserTool(driver=driver, reach=_reach())  # type: ignore[arg-type]
    tool.ask_outside = lambda question: False  # type: ignore[attr-defined]
    out = tool.run(action="navigate", url="https://evil.example/")
    assert out.startswith("error:") and "a person was asked and refused" in out
    assert driver.visited == []
    # ...and nothing was approved by the refusal.
    assert tool.run(action="navigate", url="https://evil.example/").startswith("error:")


def test_without_a_list_nobody_is_asked() -> None:
    driver = _Driver()
    tool = BrowserTool(driver=driver, reach=BrowserReach(floor=_public))  # type: ignore[arg-type]
    tool.ask_outside = lambda question: pytest.fail("asked with no site list")  # type: ignore[attr-defined]
    assert not tool.run(action="navigate", url="https://evil.example/").startswith("error:")


# --- the guard -------------------------------------------------------------------------------------


class _Session:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send(self, method: str, params: dict[str, Any]) -> None:
        self.sent.append((method, params))


def _paused(url: str, kind: str, frame: str, rid: str) -> dict[str, Any]:
    return {"requestId": rid, "request": {"url": url}, "resourceType": kind, "frameId": frame}


def test_the_guard_holds_only_top_level_navigations_to_the_list() -> None:
    reach = _reach()
    guard = RequestGuard(reach.permits, listed=reach.listed, cache=False)
    session = _Session()
    guard.on_paused(session, _paused("https://evil.example/", "Document", "MAIN", "top"), main_frame="MAIN")
    guard.on_paused(session, _paused("https://evil.example/embed", "Document", "CHILD", "frame"), main_frame="MAIN")
    guard.on_paused(session, _paused("https://evil.example/a.png", "Image", "MAIN", "image"), main_frame="MAIN")
    guard.on_paused(session, _paused("https://example.com/", "Document", "MAIN", "listed"), main_frame="MAIN")
    verdicts = {params["requestId"]: method for method, params in session.sent}
    assert verdicts == {
        "top": "Fetch.failRequest",
        "frame": "Fetch.continueRequest",
        "image": "Fetch.continueRequest",
        "listed": "Fetch.continueRequest",
    }
    blocked = guard.take()
    assert blocked == ["https://evil.example/"]
    assert "site list" in guard.refusal(blocked[0])
    assert "internal address" in guard.refusal("http://10.0.0.1/")


def test_an_unknown_main_frame_judges_every_document() -> None:
    reach = _reach()
    guard = RequestGuard(reach.permits, listed=reach.listed, cache=False)
    session = _Session()
    guard.on_paused(session, _paused("https://evil.example/embed", "Document", "CHILD", "frame"))
    assert session.sent[0][0] == "Fetch.failRequest"


# --- a real browser ------------------------------------------------------------------------------


class _Site(BaseHTTPRequestHandler):
    port = 0

    def log_message(self, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        other = f"http://localhost:{self.port}"
        pages = {
            "/link": (200, f'<a href="{other}/away">away</a>', {}),
            "/redirect": (302, "", {"Location": f"{other}/away"}),
            "/framed": (200, f'<p>host</p><iframe src="{other}/embed"></iframe>', {}),
            "/embed": (200, "<p>EMBEDDED</p>", {}),
            "/away": (200, "<p>AWAY</p>", {}),
            "/home": (200, "<p>home</p>", {}),
        }
        status, body, headers = pages.get(self.path.split("?")[0], (404, "no", {}))
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    _Site.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{_Site.port}"
    server.shutdown()


@pytest.fixture()
def driver() -> Iterator[PlaywrightDriver]:
    """`127.0.0.1` is the listed site and `localhost` an unlisted one; the floor lets both through,
    so what refuses `localhost` here is the list and nothing else."""
    reach = BrowserReach(
        ("127.0.0.1",), floor=lambda url: urlparse(url).hostname in {"127.0.0.1", "localhost"}, owned=frozenset
    )
    try:
        d = PlaywrightDriver(headless=True, reach=reach)
    except Exception as exc:  # noqa: BLE001 — no Chromium here (CI): these tests need a real browser
        pytest.skip(f"chromium unavailable: {exc}")
    yield d
    d.close()


def test_a_real_click_off_the_list_is_refused(site: str, driver: PlaywrightDriver) -> None:
    elements = driver.navigate(f"{site}/link")
    with pytest.raises(ValueError, match="outside the browser's site list"):
        driver.click(elements[0].ref)
    assert "AWAY" not in driver.page_html()


def test_a_real_redirect_off_the_list_is_refused_and_the_next_page_loads(site: str, driver: PlaywrightDriver) -> None:
    with pytest.raises(ValueError, match="outside the browser's site list"):
        driver.navigate(f"{site}/redirect")
    driver.navigate(f"{site}/home")
    assert "home" in driver.page_text()


def test_a_real_frame_from_another_host_still_loads_inside_a_listed_page(site: str, driver: PlaywrightDriver) -> None:
    """The main frame's id is read from the browser when the driver attaches; this is the test that
    it is the right one, and that it does not change when the page navigates."""
    driver.navigate(f"{site}/home")
    driver.navigate(f"{site}/framed")
    driver._page.wait_for_timeout(500)
    bodies = [frame.content() for frame in driver._page.frames]
    assert any("EMBEDDED" in body for body in bodies)
    assert driver.guard.blocked_requests == 0
