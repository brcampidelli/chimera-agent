"""Study 24, M7: an opt-in listing of the viewport's elements and a count of the rest.

Step 1 (`bench/browser_element_list`) measured four in five listed elements off-screen, and one long
article at 2,112 elements and 60 k characters handed to the model on every read. The proposal ranked
first was to list what is in the viewport and count the rest, keeping everything reachable through
`find` and scrolling. Whether that helps or hurts a task is `bench/browser_viewport_tasks`'s question;
this file pins the three things the measurement relies on:

* **Off, nothing changes** — not the listing, not `find`, not the schema the model is shown.
* **On, the viewport is listed and the rest is a count** that says how to reach it.
* **On, nothing becomes unreachable** — `find` names every matching element by ref, off-screen ones
  included, and that ref still clicks; `scroll` moves the viewport.

The unit tests use a fake driver; the last one drives a real Chromium against a local page and skips
where it cannot launch (CI has no browser binary).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from chimera.governance.ledger_tool import FENCE_CLOSE, FENCE_OPEN
from chimera.tools.browser import (
    BrowserTool,
    Element,
    find_elements,
    render_elements,
    render_viewport_first,
)

# A page as the driver reports it: three elements in view, four below, one above, one the driver
# could not place (None) — which must be listed, never counted away.
PAGE = [
    Element("e1", "link", "Home", "in"),
    Element("e2", "textbox", "Search", "in"),
    Element("e3", "link", "Back to top", "above"),
    Element("e4", "link", "Docs", "in"),
    Element("e5", "link", "Wave power", "below"),
    Element("e6", "link", "Tidal stream generator", "below"),
    Element("e7", "button", "Subscribe", "below"),
    Element("e8", "link", "Corrections", "below"),
    Element("e9", "link", "Unplaced", None),
]
SCROLLED = [
    Element("e1", "link", "Home", "above"),
    Element("e5", "link", "Wave power", "in"),
    Element("e6", "link", "Tidal stream generator", "in"),
]
TEXT = "Tidal power\nSee also\nWave power\nTidal stream generator\nCorrections and clarifications"


class _Driver:
    """The fake page: every element keeps its ref wherever it sits, as the real driver's stamp does."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def navigate(self, url: str) -> list[Element]:
        self.calls.append(f"navigate:{url}")
        return PAGE

    def read(self) -> list[Element]:
        self.calls.append("read")
        return PAGE

    def click(self, ref: str) -> list[Element]:
        self.calls.append(f"click:{ref}")
        if ref not in {el.ref for el in PAGE}:
            raise KeyError(f"unknown ref {ref!r}")
        return [Element("e1", "heading", f"clicked {ref}", "in")]

    def type_text(self, ref: str, text: str) -> list[Element]:
        self.calls.append(f"type:{ref}:{text}")
        return PAGE

    def back(self) -> list[Element]:
        self.calls.append("back")
        return PAGE

    def scroll(self, direction: str) -> list[Element]:
        self.calls.append(f"scroll:{direction}")
        return SCROLLED

    def page_html(self) -> str:
        return "<html><body><p>Tidal power</p></body></html>"

    def page_text(self) -> str:
        self.calls.append("page_text")
        return TEXT

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.scrape.ssrf as ssrf

    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.216.34"])


def _tool(*, viewport_first: bool) -> tuple[BrowserTool, _Driver]:
    driver = _Driver()
    return BrowserTool(driver=driver, viewport_first=viewport_first), driver


# --- off: byte-identical to the listing every run has read ---------------------------------------


def test_off_by_default_every_element_is_listed_whatever_its_place() -> None:
    tool, driver = _tool(viewport_first=False)
    out = tool.run(action="navigate", url="https://example.com")
    # The exact bytes, pinned: the default listing ignores `where` altogether.
    assert out == (
        f"{FENCE_OPEN}\n"
        "[e1] link: Home\n[e2] textbox: Search\n[e3] link: Back to top\n[e4] link: Docs\n"
        "[e5] link: Wave power\n[e6] link: Tidal stream generator\n[e7] button: Subscribe\n"
        "[e8] link: Corrections\n[e9] link: Unplaced\n"
        f"{FENCE_CLOSE}"
    )
    assert out == render_elements(PAGE)
    assert "outside the viewport" not in out
    assert driver.calls == ["navigate:https://example.com"]


def test_off_by_default_the_schema_and_description_are_the_class_ones() -> None:
    tool = BrowserTool(driver=_Driver())
    assert tool.viewport_first is False
    assert tool.parameters is BrowserTool.parameters
    assert tool.description is BrowserTool.description
    assert "scroll" not in tool.parameters["properties"]["action"]["enum"]
    assert "direction" not in tool.parameters["properties"]


def test_off_find_answers_with_text_only_and_never_reads_the_elements() -> None:
    tool, driver = _tool(viewport_first=False)
    out = tool.run(action="find", query="wave")
    assert "Wave power" in out and "[e5]" not in out
    assert driver.calls == ["page_text"]  # no snapshot, exactly as before


def test_off_scroll_is_an_unknown_action_as_it_always_was() -> None:
    tool, driver = _tool(viewport_first=False)
    out = tool.run(action="scroll", direction="down")
    assert out == (
        "error: unknown action 'scroll' (use navigate/read/read_text/find/click/type/back/screenshot)"
    )
    assert driver.calls == []


# --- on: the viewport, then a count ---------------------------------------------------------------


def test_on_lists_the_viewport_and_counts_the_rest() -> None:
    tool, _ = _tool(viewport_first=True)
    out = tool.run(action="navigate", url="https://example.com")
    assert out.startswith(FENCE_OPEN) and out.rstrip().endswith(FENCE_CLOSE)
    for listed in ("[e1] link: Home", "[e2] textbox: Search", "[e4] link: Docs"):
        assert listed in out
    for summarised in ("[e3]", "[e5]", "[e6]", "[e7]", "[e8]"):
        assert summarised not in out
    assert "and 5 more interactive elements outside the viewport (4 below, 1 above)" in out
    assert "find (query)" in out and "scroll (direction)" in out  # the way to them is named


def test_on_an_element_the_driver_did_not_place_is_listed_not_counted() -> None:
    out = render_viewport_first(PAGE)
    assert "[e9] link: Unplaced" in out


def test_on_with_everything_in_view_the_output_is_the_default_one() -> None:
    in_view = [Element("e1", "link", "Home", "in"), Element("e2", "link", "Docs", None)]
    assert render_viewport_first(in_view) == render_elements(in_view)


def test_on_an_empty_viewport_still_says_what_is_outside_it() -> None:
    out = render_viewport_first([Element("e1", "link", "Far", "below")])
    assert "(no interactive elements in the viewport)" in out
    assert "and 1 more interactive elements outside the viewport (1 below)" in out


def test_on_every_listing_action_uses_the_viewport_rendering() -> None:
    tool, _ = _tool(viewport_first=True)
    for kwargs in ({"action": "read"}, {"action": "type", "ref": "e2", "text": "x"}, {"action": "back"}):
        assert "outside the viewport" in tool.run(**kwargs), kwargs


def test_on_the_schema_offers_scroll_without_touching_the_class_schema() -> None:
    tool = BrowserTool(driver=_Driver(), viewport_first=True)
    actions = tool.parameters["properties"]["action"]["enum"]
    assert "scroll" in actions and tool.parameters["properties"]["direction"]["enum"] == ["down", "up"]
    assert "scroll" in tool.description and "outside" in tool.description
    # A default instance built afterwards still reads the untouched class attributes.
    assert "scroll" not in BrowserTool.parameters["properties"]["action"]["enum"]
    assert "scroll" not in BrowserTool(driver=_Driver()).to_openai_schema()["function"]["description"]


# --- on: nothing becomes unreachable -------------------------------------------------------------


def test_on_find_names_off_screen_elements_by_ref_and_the_ref_clicks() -> None:
    tool, driver = _tool(viewport_first=True)
    listing = tool.run(action="navigate", url="https://example.com")
    assert "[e8]" not in listing  # summarised away…
    found = tool.run(action="find", query="corrections")
    assert "Corrections and clarifications" in found  # …the text match, as before…
    assert "[e8] link: Corrections (below)" in found  # …and the element, by ref, with its place
    assert "clicked e8" in tool.run(action="click", ref="e8")  # and the ref acts
    assert driver.calls[-1] == "click:e8"


def test_on_find_marks_elements_in_view_as_such() -> None:
    out = find_elements(PAGE, "home")
    assert "[e1] link: Home (in view)" in out


def test_on_find_with_a_url_matches_the_page_it_just_loaded() -> None:
    tool, driver = _tool(viewport_first=True)
    out = tool.run(action="find", query="wave", url="https://example.com")
    assert "[e5] link: Wave power (below)" in out
    assert driver.calls == ["navigate:https://example.com", "page_text"]  # no second snapshot


def test_on_find_reports_when_no_element_matches() -> None:
    assert "(no interactive element named like 'zzz')" in find_elements(PAGE, "zzz")


def test_on_find_caps_the_element_matches() -> None:
    many = [Element(f"e{i}", "link", f"reply {i}", "below") for i in range(50)]
    out = find_elements(many, "reply", max_hits=40)
    assert "50 element(s)" in out and "[e39]" in out and "[e40]" not in out
    assert "10 more matching elements" in out


def test_on_scroll_moves_the_viewport_and_lists_it() -> None:
    tool, driver = _tool(viewport_first=True)
    out = tool.run(action="scroll", direction="down")
    assert driver.calls == ["scroll:down"]
    assert "[e5] link: Wave power" in out and "[e1]" not in out
    assert "and 1 more interactive elements outside the viewport (1 above)" in out
    tool.run(action="scroll")
    assert driver.calls[-1] == "scroll:down"  # down is the default
    tool.run(action="scroll", direction="UP")
    assert driver.calls[-1] == "scroll:up"


def test_on_scroll_refuses_a_direction_it_does_not_have() -> None:
    tool, driver = _tool(viewport_first=True)
    assert tool.run(action="scroll", direction="left").startswith("error:")
    assert driver.calls == []


def test_on_a_driver_that_cannot_scroll_says_so() -> None:
    class _NoScroll(_Driver):
        scroll = None  # type: ignore[assignment]

    tool = BrowserTool(driver=_NoScroll(), viewport_first=True)
    assert tool.run(action="scroll") == "error: this browser cannot scroll"


# --- the setting reaches the tool -----------------------------------------------------------------


def test_the_setting_is_off_by_default_and_reaches_the_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.config import Settings, get_settings
    from chimera.tools.builtin import default_registry

    assert Settings.model_fields["browser_viewport_first"].default is False
    assert Settings.model_fields["browser_viewport_first"].validation_alias == "CHIMERA_BROWSER_VIEWPORT_FIRST"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    for value, expected in (("", False), ("true", True)):
        monkeypatch.setenv("CHIMERA_BROWSER_VIEWPORT_FIRST", value)
        get_settings.cache_clear()
        browser = default_registry(tmp_path).get("browser")
        assert isinstance(browser, BrowserTool)
        assert browser.viewport_first is expected, value
    get_settings.cache_clear()


# --- the real driver places elements and scrolls -------------------------------------------------

_TALL = (
    b"<!doctype html><html><head><style>body{margin:0} .gap{height:3000px}</style></head><body>"
    b"<a href='#top'>Top link</a><div class='gap'></div><a href='#far'>Far link</a></body></html>"
)


class _Tall(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_TALL)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def tall_page() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Tall)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_the_real_driver_places_elements_and_scrolls(tall_page: str) -> None:
    from chimera.tools.browser_playwright import PlaywrightDriver

    try:
        driver = PlaywrightDriver(headless=True, allowed=lambda url: urlparse(url).hostname == "127.0.0.1")
    except Exception as exc:  # noqa: BLE001 — no Chromium here (CI)
        pytest.skip(f"chromium unavailable: {exc}")
    try:
        first = {el.name: el.where for el in driver.navigate(tall_page)}
        assert first == {"Top link": "in", "Far link": "below"}
        for _ in range(5):
            after = {el.name: el.where for el in driver.scroll("down")}
        assert after == {"Top link": "above", "Far link": "in"}
        back_up = {el.name: el.where for el in driver.scroll("up")}
        assert back_up["Far link"] == "below"
    finally:
        driver.close()
