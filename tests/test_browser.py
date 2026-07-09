"""Tests for the browser tool (M13 D1) — accessibility-tree navigation, via a fake driver."""

from __future__ import annotations

import pytest

from chimera.governance.ledger import FETCH_TOOLS
from chimera.governance.ledger_tool import FENCE_CLOSE, FENCE_OPEN
from chimera.tools import browser as browser_mod
from chimera.tools.browser import BrowserTool, Element, render_elements


class _FakeDriver:
    """An in-memory page model: navigate/click swap the visible elements; records calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._home = [Element("e1", "link", "Docs"), Element("e2", "textbox", "Search")]
        self._docs = [Element("e1", "heading", "Documentation")]

    def navigate(self, url: str) -> list[Element]:
        self.calls.append(f"navigate:{url}")
        return self._home

    def read(self) -> list[Element]:
        self.calls.append("read")
        return self._home

    def click(self, ref: str) -> list[Element]:
        self.calls.append(f"click:{ref}")
        if ref not in {"e1", "e2"}:
            raise KeyError(f"unknown ref {ref!r}")
        return self._docs

    def type_text(self, ref: str, text: str) -> list[Element]:
        self.calls.append(f"type:{ref}:{text}")
        return self._home

    def back(self) -> list[Element]:
        self.calls.append("back")
        return self._home

    def page_html(self) -> str:
        self.calls.append("page_html")
        return "<html><body><h1>Docs</h1><p>Hello world body text.</p></body></html>"

    def page_text(self) -> str:
        self.calls.append("page_text")
        return "Docs\nHello world body text.\nContact us at support@example.com."

    def close(self) -> None:
        self.calls.append("close")


def _tool() -> tuple[BrowserTool, _FakeDriver]:
    driver = _FakeDriver()
    return BrowserTool(driver=driver), driver


# --- rendering + fencing ----------------------------------------------------------------


def test_render_is_data_fenced() -> None:
    out = render_elements([Element("e1", "link", "Home")])
    assert out.startswith(FENCE_OPEN) and out.rstrip().endswith(FENCE_CLOSE)
    assert "[e1] link: Home" in out


def test_render_empty_page() -> None:
    assert "(no interactive elements)" in render_elements([])


# --- dispatch ---------------------------------------------------------------------------


def test_navigate_lists_elements() -> None:
    tool, driver = _tool()
    out = tool.run(action="navigate", url="https://example.com")
    assert "[e1] link: Docs" in out and "[e2] textbox: Search" in out
    assert driver.calls == ["navigate:https://example.com"]
    assert FENCE_OPEN in out  # web content is fenced


def test_click_and_type_by_ref() -> None:
    tool, driver = _tool()
    assert "Documentation" in tool.run(action="click", ref="e1")
    tool.run(action="type", ref="e2", text="hello")
    assert driver.calls == ["click:e1", "type:e2:hello"]


def test_unknown_ref_is_an_error_not_a_crash() -> None:
    tool, _ = _tool()
    assert tool.run(action="click", ref="e99").startswith("error:")


def test_missing_action_args() -> None:
    tool, _ = _tool()
    assert tool.run(action="navigate").startswith("error:")  # no url
    assert tool.run(action="click").startswith("error:")  # no ref
    assert tool.run(action="frobnicate").startswith("error:")  # unknown action


def test_missing_extra_gives_install_hint() -> None:
    # No driver injected AND playwright not installed -> friendly hint, not an ImportError.
    tool = BrowserTool()
    out = tool.run(action="read")
    assert "chimera-agent[browser]" in out and out.startswith("error:")


# --- reading rendered text (read_text / find) -------------------------------------------


def test_read_text_returns_markdown_when_extra_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_mod, "_html_to_markdown", lambda html: "# Docs\n\nHello world body text.")
    tool, driver = _tool()
    out = tool.run(action="read_text")
    assert "# Docs" in out and "Hello world body text." in out
    assert FENCE_OPEN in out and out.rstrip().endswith(FENCE_CLOSE)  # untrusted -> fenced
    assert "page_html" in driver.calls  # markdown path reads the rendered HTML


def test_read_text_falls_back_to_plain_when_extra_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # _html_to_markdown returns None when the 'documents' extra is missing -> use inner_text.
    monkeypatch.setattr(browser_mod, "_html_to_markdown", lambda html: None)
    tool, driver = _tool()
    out = tool.run(action="read_text")
    assert "Hello world body text." in out and FENCE_OPEN in out
    assert "page_text" in driver.calls


def test_read_text_with_url_navigates_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_mod, "_html_to_markdown", lambda html: None)
    tool, driver = _tool()
    tool.run(action="read_text", url="https://example.com")
    assert driver.calls[0] == "navigate:https://example.com"


def test_find_returns_matching_lines() -> None:
    tool, _ = _tool()
    out = tool.run(action="find", query="support@example")
    assert "support@example.com" in out and "match(es)" in out and FENCE_OPEN in out


def test_find_reports_when_absent() -> None:
    tool, _ = _tool()
    out = tool.run(action="find", query="nonexistent-zzz")
    assert "not found" in out and FENCE_OPEN in out


def test_find_needs_a_query() -> None:
    tool, _ = _tool()
    assert tool.run(action="find").startswith("error:")


def test_new_actions_are_advertised() -> None:
    actions = BrowserTool.parameters["properties"]["action"]["enum"]
    assert "read_text" in actions and "find" in actions


# --- governance -------------------------------------------------------------------------


def test_browser_is_a_fetch_tool() -> None:
    # Consuming a page taints the run — the browser must be classified as untrusted-content fetch.
    assert "browser" in FETCH_TOOLS
