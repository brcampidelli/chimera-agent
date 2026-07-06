"""Tests for the browser tool (M13 D1) — accessibility-tree navigation, via a fake driver."""

from __future__ import annotations

from chimera.governance.ledger import FETCH_TOOLS
from chimera.governance.ledger_tool import FENCE_CLOSE, FENCE_OPEN
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


# --- governance -------------------------------------------------------------------------


def test_browser_is_a_fetch_tool() -> None:
    # Consuming a page taints the run — the browser must be classified as untrusted-content fetch.
    assert "browser" in FETCH_TOOLS
