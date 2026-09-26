"""The two driver probes of walls v2, on a real Chromium against a local server (study 25, S11).

- ``settle`` waits for the page's ``load`` event, bounded: a frame a late script adds is in the page
  after it and not before, and a page that never finishes loading costs the bound, not more.
- ``frame_boxes`` reads the browser's frame tree, which holds a frame inside a **closed** shadow
  root that the serialised DOM (``page_html``) does not show and no page script can reach — the
  Turnstile case — with the size it is drawn at, 0 × 0 when it is not drawn.

Skipped where Chromium cannot launch (CI has no browser binary); the logic that uses these probes is
tested without a browser in ``test_a_late_or_shadowed_widget_still_hands_over.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from chimera.tools.browser_playwright import PlaywrightDriver

_LATE_DELAY_S = 0.8  # the late script's delay: well past domcontentloaded on a local page
_HANG_S = 3.0  # an image that takes longer than any bound the tests give `settle`

_CLOSED = """
<p>A login form with a widget in it.</p>
<chimera-widget id="shown"></chimera-widget>
<chimera-widget id="hidden"></chimera-widget>
<iframe src="/widget?in=document" width="120" height="40" style="border:0"></iframe>
<script>
  customElements.define('chimera-widget', class extends HTMLElement {
    constructor() {
      super();
      const root = this.attachShadow({mode: 'closed'});
      const hidden = this.id === 'hidden' ? 'display:none' : 'border:0';
      root.innerHTML =
        `<iframe src="/widget?in=${this.id}" width="300" height="65" style="${hidden}">`;
    }
  });
</script>
"""

_PAGES: dict[str, str] = {
    "/late": '<p>contact form</p><script async src="/slow.js"></script>',
    "/slow.js": (
        "document.body.insertAdjacentHTML('beforeend', '<iframe src=\"/widget?late\"></iframe>');"
    ),
    "/hang": '<p>a page still loading</p><img src="/hang.png">',
    "/closed": _CLOSED,
    "/widget": "<p>widget</p>",
}


class _Site(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        path = self.path.split("?")[0]
        if path == "/slow.js":
            time.sleep(_LATE_DELAY_S)
        if path == "/hang.png":
            time.sleep(_HANG_S)
        body = _PAGES.get(path, "").encode("utf-8")
        kind = "application/javascript" if path.endswith(".js") else "text/html; charset=utf-8"
        self.send_response(200 if path in _PAGES else 404)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture()
def driver() -> Iterator[PlaywrightDriver]:
    try:
        d = PlaywrightDriver(
            headless=True, allowed=lambda url: urlparse(url).hostname == "127.0.0.1"
        )
    except Exception as exc:  # noqa: BLE001 — no Chromium here (CI): they need a real browser
        pytest.skip(f"chromium unavailable: {exc}")
    yield d
    d.close()


def test_a_frame_a_late_script_adds_is_there_after_settle_and_not_before(
    site: str, driver: PlaywrightDriver
) -> None:
    driver.navigate(f"{site}/late")
    assert "/widget?late" not in driver.page_html(), "the action returns before the late script"
    driver.settle(_LATE_DELAY_S * 5)
    assert "/widget?late" in driver.page_html()


def test_settle_is_a_bound_not_a_condition(site: str, driver: PlaywrightDriver) -> None:
    driver.navigate(f"{site}/hang")
    started = time.monotonic()
    driver.settle(0.5)
    assert time.monotonic() - started < _HANG_S - 1.0, "it waited for the page, not for its bound"


def test_the_frame_tree_holds_a_frame_in_a_closed_shadow_root(
    site: str, driver: PlaywrightDriver
) -> None:
    driver.navigate(f"{site}/closed")
    driver.settle(2.0)
    assert "in=shown" not in driver.page_html(), "the serialised DOM cannot show a shadow root"

    boxes = {urlparse(address).query: (w, h) for address, w, h in driver.frame_boxes(
        lambda address: "/widget" in address
    )}
    assert boxes["in=shown"] == (300, 65)
    assert boxes["in=hidden"] == (0.0, 0.0), "in the tree, and not drawn"
    assert boxes["in=document"] == (120, 40), "a frame in the document is in the tree too"


def test_keep_decides_which_frames_are_measured(site: str, driver: PlaywrightDriver) -> None:
    driver.navigate(f"{site}/closed")
    driver.settle(2.0)
    asked: list[str] = []

    def keep(address: str) -> bool:
        asked.append(address)
        return "in=shown" in address

    found = driver.frame_boxes(keep)
    assert [urlparse(a).query for a, _, _ in found] == ["in=shown"]
    assert len(asked) == 3, "every child frame is offered to keep, the page itself is not"
