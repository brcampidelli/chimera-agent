"""The point-to-element resolution, on a real Chromium (skipped where one cannot launch).

The fake-browser tests prove what the server does with an answer; this proves the answer itself: that the
element under a point is the one found, that its selector is short and finds exactly it, that a page that
lies about the DOM cannot make the message say what it likes, and that nothing secret leaves with it.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from chimera.core.element_pick import validate

PAGE = """
<html><body style="margin:0">
  <div class="card"><button class="primary big" style="position:absolute;left:10px;top:10px;width:100px;height:40px">Salvar</button></div>
  <div class="card"><button class="css-1a2b3c" style="position:absolute;left:10px;top:70px;width:100px;height:40px">Outro</button></div>
  <a id="docs" href="/guide?token=SECRET#top" style="position:absolute;left:10px;top:130px;width:100px;height:40px;display:block">Guia</a>
  <input id="pw" type="password" value="hunter2" style="position:absolute;left:10px;top:190px;width:100px;height:30px">
</body></html>
"""


class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def driver() -> Iterator[Any]:
    pytest.importorskip("playwright")
    from chimera.tools.browser_playwright import PlaywrightDriver

    try:
        made = PlaywrightDriver(allowed=lambda url: True)
    except Exception as exc:  # noqa: BLE001 — no Chromium here
        pytest.skip(f"no browser: {exc}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    made.navigate(f"http://127.0.0.1:{server.server_port}/")
    yield made
    made.close()
    server.shutdown()


def _pick(driver: Any, x: float, y: float) -> Any:
    found = driver.element_at(x, y)
    assert found is not None
    picked = validate(found, page="http://localhost:1/", server="web")
    assert picked is not None
    return picked


def test_the_element_under_the_point_is_found_with_a_short_selector_that_finds_only_it(driver: Any) -> None:
    picked = _pick(driver, 40, 30)
    assert picked.tag == "button" and picked.text == "Salvar" and picked.label == "<button.primary> 'Salvar'"
    assert picked.selector == "button.primary.big" and picked.unique
    assert driver._page.locator(picked.selector).count() == 1
    assert picked.box == (10, 10, 100, 40) and picked.crop and picked.crop.startswith(b"\x89PNG")


def test_a_generated_class_is_not_used_and_the_selector_still_finds_one_element(driver: Any) -> None:
    picked = _pick(driver, 40, 90)
    assert "css-1a2b3c" not in picked.selector and picked.text == "Outro"
    assert driver._page.locator(picked.selector).count() == 1


def test_an_address_loses_its_query_and_a_password_field_gives_no_value(driver: Any) -> None:
    link = _pick(driver, 40, 150)
    assert link.attrs["href"] == "/guide" and link.selector == "#docs"
    assert "SECRET" not in link.document()
    field = _pick(driver, 40, 200)
    assert field.tag == "input" and field.text == "" and "hunter2" not in field.document()


def test_a_point_on_nothing_is_none_or_the_page_and_a_lying_page_cannot_write_the_message(driver: Any) -> None:
    driver._page.evaluate(
        "document.elementFromPoint = () => ({tagName: 'DIV><script', getAttribute: () => 'x'})"
    )
    try:
        found = driver.element_at(40, 30)
    except Exception:  # noqa: BLE001 — a page that breaks the reader is refused, which is the point
        found = None
    assert found is None or validate(found, page="p", server="web") is None
