"""The wall detector's two measured gaps, closed (study 25, S11, walls v2).

The live-page run (`bench/browser_element_list/RESULTS-situation.md`) named two walls the first
detector could not see, each with its cause pinned:

1. **hCaptcha** injects its checkbox frame after ``domcontentloaded``, the moment every action
   returns at. The fix waits, bounded, for the page's ``load`` event before the look — only after
   an action that can load a document, so a plain look costs nothing new.
2. **Turnstile** draws its frame inside a closed shadow root, which the serialised DOM does not
   hold and no script can walk. The fix reads the driver's frame tree beside the HTML, and counts a
   frame from it only when it is drawn at a size a person could use.

The drivers below are fakes: `settle` is what makes a late frame appear, and `frame_boxes` is the
frame tree. A driver without either (every fake in the older tests) must behave exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from chimera.tools.browser import BrowserTool, Element
from chimera.tools.browser_situation import (
    SETTLE_SECONDS,
    BrowserSituation,
    detect_wall,
    is_provider_frame,
)

_ARTICLE = (
    '<html><body><a data-chimera-ref="e1" href="/">Home</a>'
    '<input data-chimera-ref="e2" type="email" name="login"><p>Sign up for the newsletter.</p>'
    "</body></html>"
)
_HCAPTCHA = (
    '<iframe src="https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html'
    '#frame=checkbox&id=0x&host=example.com&size=normal"></iframe>'
)
_TURNSTILE = (
    "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv/x/"
    "0x4AAA/auto/fbE/new/normal/auto/"
)
_PASSWORD = '<input data-chimera-ref="e3" type="password" name="password">'

PAGE = "https://shop.example.com/contact"


class _Driver:
    """A page whose frames arrive late (``late``, added by the first `settle`) or live only in the
    frame tree (``tree``: address, width, height). Every call recorded."""

    def __init__(
        self,
        html: str = _ARTICLE,
        *,
        late: str = "",
        tree: list[tuple[str, float, float]] | None = None,
        settle_raises: bool = False,
        tree_raises: bool = False,
    ) -> None:
        self.html = html
        self.late = late
        self.tree = tree or []
        self.settle_raises = settle_raises
        self.tree_raises = tree_raises
        self.url = ""
        self.calls: list[str] = []
        self.asked: list[str] = []  # the addresses `keep` was asked about
        self.measured: list[str] = []  # the ones it accepted, whose box a real driver would fetch

    def _elements(self) -> list[Element]:
        return [Element("e1", "link", "Home"), Element("e2", "textbox", "login")]

    def navigate(self, url: str) -> list[Element]:
        self.calls.append("navigate")
        self.url = url
        return self._elements()

    def read(self) -> list[Element]:
        self.calls.append("read")
        return self._elements()

    def click(self, ref: str) -> list[Element]:
        self.calls.append("click")
        return self._elements()

    def type_text(self, ref: str, text: str) -> list[Element]:
        self.calls.append("type")
        return self._elements()

    def back(self) -> list[Element]:
        self.calls.append("back")
        return self._elements()

    def page_html(self) -> str:
        return self.html

    def page_text(self) -> str:
        return "text"

    def screenshot(self, path: str) -> None:  # pragma: no cover - not exercised here
        self.calls.append("screenshot")

    def frame(self) -> None:
        return None

    def close(self) -> None:
        self.calls.append("close")

    def settle(self, seconds: float) -> None:
        self.calls.append(f"settle:{seconds}")
        if self.settle_raises:
            raise RuntimeError("the page navigated while waiting")
        if self.late:
            self.html = self.html.replace("</body>", self.late + "</body>")
            self.late = ""

    def frame_boxes(self, keep: Callable[[str], bool]) -> list[tuple[str, float, float]]:
        self.calls.append("frame_boxes")
        if self.tree_raises:
            raise RuntimeError("frame detached")
        kept = []
        for address, width, height in self.tree:
            self.asked.append(address)
            if keep(address):
                self.measured.append(address)
                kept.append((address, width, height))
        return kept


class _Plain:
    """The oldest fake shape: no `settle`, no `frame_boxes`."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.url = ""

    def navigate(self, url: str) -> list[Element]:
        self.url = url
        return [Element("e1", "link", "Home")]

    def page_html(self) -> str:
        return self.html

    def frame(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.scrape import ssrf

    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.216.34"])


def _tool(driver: object, **kw: float) -> BrowserTool:
    return BrowserTool(driver=driver, situation=BrowserSituation(**kw))  # type: ignore[arg-type]


# --- 1. the late widget ---------------------------------------------------------------------------


def test_a_frame_injected_after_domcontentloaded_hands_over_on_arrival() -> None:
    """The hCaptcha case: nothing at the first look, the checkbox once the page has loaded."""
    driver = _Driver(late=_HCAPTCHA)
    out = _tool(driver).run(action="navigate", url=PAGE)
    assert out.startswith("handover:") and "a captcha" in out and "hCaptcha" in out
    assert f"settle:{SETTLE_SECONDS}" in driver.calls


def test_without_the_wait_the_late_frame_is_missed_as_in_v1() -> None:
    """The control for the test above: the same page, the wait off, and the run goes on."""
    driver = _Driver(late=_HCAPTCHA)
    out = _tool(driver, settle_seconds=0).run(action="navigate", url=PAGE)
    assert not out.startswith("handover:")
    assert not any(call.startswith("settle") for call in driver.calls)


@pytest.mark.parametrize("action", ["navigate", "click", "back", "read_text", "find"])
def test_every_action_that_can_load_a_document_waits_once(action: str) -> None:
    driver = _Driver()
    _tool(driver).run(action=action, url=PAGE, ref="e1", query="x")
    assert [c for c in driver.calls if c.startswith("settle")] == [f"settle:{SETTLE_SECONDS}"]


@pytest.mark.parametrize("action", ["read", "type"])
def test_a_look_that_loads_nothing_does_not_wait(action: str) -> None:
    """A page that never reaches ``load`` would otherwise charge the wait on every look."""
    driver = _Driver()
    driver.url = PAGE
    _tool(driver).run(action=action, ref="e1", text="hello")
    assert not any(call.startswith("settle") for call in driver.calls)


def test_a_read_without_an_address_does_not_wait() -> None:
    driver = _Driver()
    driver.url = PAGE
    _tool(driver).run(action="read_text")
    assert not any(call.startswith("settle") for call in driver.calls)


def test_a_wait_that_fails_still_looks() -> None:
    driver = _Driver(html=_ARTICLE.replace("</body>", _PASSWORD + "</body>"), settle_raises=True)
    out = _tool(driver).run(action="navigate", url=PAGE)
    assert out.startswith("handover:") and "a sign-in" in out


def test_an_error_neither_waits_nor_looks() -> None:
    driver = _Driver()
    out = _tool(driver).run(action="navigate")  # no url: the tool's own error
    assert out == "error: navigate needs a url"
    assert driver.calls == []


# --- 2. the frame the HTML does not show ----------------------------------------------------------


def test_a_turnstile_in_a_closed_shadow_root_hands_over_from_the_frame_tree() -> None:
    """Not in the HTML at all — only the frame tree has it, drawn at its measured 300 × 65."""
    driver = _Driver(tree=[(_TURNSTILE, 300, 65)])
    assert _TURNSTILE not in driver.page_html()
    out = _tool(driver).run(action="navigate", url=PAGE)
    assert out.startswith("handover:") and "Turnstile" in out


@pytest.mark.parametrize("size", [(0, 0), (1, 1), (300, 0)])
def test_a_frame_the_tree_holds_but_does_not_draw_is_not_a_wall(size: tuple[int, int]) -> None:
    """Hidden (0 × 0), a pixel, or collapsed: the tree has these on ordinary pages too."""
    driver = _Driver(tree=[(_TURNSTILE, *size)])
    out = _tool(driver).run(action="navigate", url=PAGE)
    assert not out.startswith("handover:")


def test_the_tree_is_matched_by_the_same_address_rules_as_the_html() -> None:
    """An invisible reCAPTCHA or Stripe's fraud frame is no wall in the tree either, even drawn."""
    tree = [
        ("https://www.google.com/recaptcha/api2/anchor?k=x&size=invisible", 256, 60),
        ("https://js.stripe.com/v3/m-outer-3437aaddcdf6922d623e172c2d6f9278.html#url=x", 300, 300),
        ("https://ads.example.net/slot/728x90", 728, 90),
    ]
    driver = _Driver(tree=tree)
    out = _tool(driver).run(action="navigate", url=PAGE)
    assert not out.startswith("handover:")
    assert driver.asked == [address for address, _, _ in tree]  # asked, and all refused


def test_only_provider_frames_are_measured() -> None:
    """The box costs two round trips; `keep` spends them on candidates only."""
    ads = [(f"https://ads.example.net/slot/{n}", 728, 90) for n in range(5)]
    driver = _Driver(tree=[*ads, (_TURNSTILE, 300, 65)])
    _tool(driver).run(action="navigate", url=PAGE)
    assert driver.measured == [_TURNSTILE]


def test_a_provider_frame_is_known_by_its_address() -> None:
    assert is_provider_frame(_TURNSTILE)
    assert is_provider_frame("https://newassets.hcaptcha.com/c/hcaptcha.html#frame=checkbox")
    assert not is_provider_frame("https://ads.example.net/slot/728x90")
    assert not is_provider_frame("about:blank")


def test_a_frame_tree_that_cannot_be_read_leaves_the_html_scan_to_run() -> None:
    driver = _Driver(html=_ARTICLE.replace("</body>", _PASSWORD + "</body>"), tree_raises=True)
    out = _tool(driver).run(action="navigate", url=PAGE)
    assert out.startswith("handover:") and "a sign-in" in out


def test_detect_wall_reads_the_frames_it_is_given() -> None:
    assert detect_wall(_ARTICLE, PAGE) is None
    wall = detect_wall(_ARTICLE, PAGE, [_TURNSTILE])
    assert wall is not None and wall.kind == "captcha"


def test_a_captcha_in_the_tree_outranks_the_login_form_around_it() -> None:
    """The Turnstile demo is a login form holding the widget; the captcha cannot be passed at all."""
    html = _ARTICLE.replace("</body>", _PASSWORD + "</body>")
    wall = detect_wall(html, PAGE, [_TURNSTILE])
    assert wall is not None and wall.kind == "captcha"


# --- 3. what did not change -----------------------------------------------------------------------


def test_an_ordinary_page_reads_byte_for_byte_as_without_the_module() -> None:
    """The wait moves only when the look happens; the list the model reads is the action's own."""
    situated = _Driver(tree=[("https://ads.example.net/slot", 728, 90)])
    plain = _Driver()
    out = _tool(situated).run(action="navigate", url=PAGE)
    plain_tool = BrowserTool(driver=plain)  # type: ignore[arg-type]
    assert out == plain_tool.run(action="navigate", url=PAGE)


def test_without_the_module_nothing_waits_and_no_frame_tree_is_read() -> None:
    driver = _Driver(late=_HCAPTCHA, tree=[(_TURNSTILE, 300, 65)])
    out = BrowserTool(driver=driver).run(action="navigate", url=PAGE)  # type: ignore[arg-type]
    assert not out.startswith("handover:")
    assert driver.calls == ["navigate"]


def test_a_driver_without_the_new_methods_behaves_as_in_v1() -> None:
    wall_page = _ARTICLE.replace("</body>", _PASSWORD + "</body>")
    assert _tool(_Plain(wall_page)).run(action="navigate", url=PAGE).startswith("handover:")
    assert not _tool(_Plain(_ARTICLE)).run(action="navigate", url=PAGE).startswith("handover:")
