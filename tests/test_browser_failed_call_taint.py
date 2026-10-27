"""A browser call that fails must taint the run only when it read something.

Live, 2026-10-26: the owner had ``CHIMERA_TRUSTED_SOURCES=*``. The paired Chrome refused
``navigate https://example.org`` before loading anything, and the refusal still tainted the run with
that URL as its source, so the next ``google_search`` was narrowed. Nothing from the page was read.

The rule these tests pin: a failure before any page was reached records nothing; a failure on a page
the browser did reach keeps the taint, unless the owner trusts that page's host.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import pytest

from chimera.config import get_settings
from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import LedgeredTool
from chimera.tools import browser as browser_module
from chimera.tools.browser import BrowserTool
from chimera.tools.browser_reach import trusted_source_matches


class _Guard:
    """The request log the real Playwright driver keeps, reduced to the two answers the tool asks for."""

    request_count = 0

    def trusted_only_since(self, start: int, top_url: str, raw: str) -> bool:
        return trusted_source_matches(urlparse(top_url).hostname or "", raw)

    def local_only_since(self, start: int, top_url: str) -> bool:
        return False


class _Page:
    def __init__(self, *, refuse_navigation: str | None = None) -> None:
        self.refuse_navigation = refuse_navigation
        self.current = ""
        self.guard = _Guard()

    @property
    def url(self) -> str:
        return self.current

    def navigate(self, url: str) -> list[Any]:
        if self.refuse_navigation is not None:
            raise RuntimeError(self.refuse_navigation)
        self.current = url
        return []

    def read(self) -> list[Any]:
        return []

    def page_html(self) -> str:
        return "<p>page body</p>"

    def page_text(self) -> str:
        return "page body"

    def close(self) -> None:
        pass


REFUSAL = "This site has not been granted to the Chimera extension"


def _raise_after_load(_html: str) -> str:
    raise RuntimeError("renderer failed after the page loaded")


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.scrape.ssrf.check_url", lambda url: None)


def _set_trusted(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    # The ledger reads the owner's list from settings; the browser reads the copy it was built with.
    monkeypatch.setattr(get_settings(), "trusted_sources", value)


def _tool(page: _Page, trusted: str) -> LedgeredTool:
    browser = BrowserTool(driver=page, trusted_sources=trusted)
    return LedgeredTool(browser, TaintLedger())


@pytest.mark.parametrize("trusted", ["", "*", "docs.python.org"])
def test_a_navigation_refused_before_any_page_taints_nothing(
    monkeypatch: pytest.MonkeyPatch, trusted: str
) -> None:
    _set_trusted(monkeypatch, trusted)
    tool = _tool(_Page(refuse_navigation=REFUSAL), trusted)
    output = tool.run(action="navigate", url="https://example.org")
    assert "error" in output
    assert not tool.ledger.run_tainted()
    assert tool.ledger.taint_sources() == []
    assert not tool.ledger.run_tainted(for_narrowing=True), "the next browser call must not be narrowed"


def test_a_refused_navigation_is_not_recorded_as_a_fetch_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_trusted(monkeypatch, "*")
    tool = _tool(_Page(refuse_navigation=REFUSAL), "*")
    tool.run(action="navigate", url="https://example.org")
    assert tool.ledger.taint_sources(for_narrowing=True) == []


def test_a_failure_on_an_untrusted_page_still_taints(monkeypatch: pytest.MonkeyPatch) -> None:
    # The page loads (the browser reached it), then the call fails while rendering it: the text the
    # model would have read is example.org's, and the owner trusts only docs.python.org.
    _set_trusted(monkeypatch, "docs.python.org")
    monkeypatch.setattr(browser_module, "_html_to_markdown", _raise_after_load)
    tool = _tool(_Page(), "docs.python.org")
    output = tool.run(action="read_text", url="https://example.org/page")
    assert "error" in output
    assert tool.ledger.run_tainted(), "a page the owner does not trust taints the run even when the call fails"
    assert any("example.org" in source for source in tool.ledger.taint_sources())


def test_a_failure_on_a_page_on_a_trusted_host_under_star_does_not_taint(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_trusted(monkeypatch, "*")
    monkeypatch.setattr(browser_module, "_html_to_markdown", _raise_after_load)
    tool = _tool(_Page(), "*")
    output = tool.run(action="read_text", url="https://example.org/page")
    assert "error" in output
    assert not tool.ledger.run_tainted(), "the quoted text can only have come from a host the owner trusts"


def test_a_refusal_leaves_the_next_navigation_free_to_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_trusted(monkeypatch, "")
    page = _Page(refuse_navigation=REFUSAL)
    tool = _tool(page, "")
    tool.run(action="navigate", url="https://example.org")
    page.refuse_navigation = None
    tool.run(action="navigate", url="https://docs.python.org/3/")
    assert page.current == "https://docs.python.org/3/"
    # The refused page left nothing behind; the page that did load is an untrusted read, and it taints
    # as it always has. Only that page is named: the refusal never became a source.
    sources = tool.ledger.taint_sources()
    assert all("example.org" not in source for source in sources)
    assert any("docs.python.org" in source for source in sources)
