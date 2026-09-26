"""The four confirmation classes are rows about gates that exist, checked against those gates.

`chimera.governance.confirmations` maps each class onto the governance vocabulary (study 25, S11).
A table like that is only worth keeping if it cannot drift from what the layers do, so each row's
browser example is driven through the layer the row names, with the real `LedgeredTool`, the real
`TaintLedger` and a `BrowserTool` over a fake page:

- **hand over** — even an approver that says yes to everything does not get a password typed;
- **confirm at action time** — after the first page, a click asks a person, and a no stops it, even
  though the request named the page;
- **pre-approved by the request** — the same click, under the ``authority`` mode, asks nobody;
- **none** — reading the page already loaded asks nobody.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from chimera.governance.confirmations import CONFIRMATIONS, ConfirmationClass, confirmation
from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import LedgeredTool
from chimera.governance.policy import Decision
from chimera.tools.base import is_refusal
from chimera.tools.browser import BrowserTool, Element
from chimera.tools.browser_situation import BrowserSituation

PAGE = "https://portal.example.gov/contact"
LOGIN_FORM = (
    '<form><input data-chimera-ref="e1" type="email" name="login">'
    '<input data-chimera-ref="e2" type="password" name="password">'
    '<button data-chimera-ref="e3">Send</button></form>'
)


class _Page:
    def __init__(self) -> None:
        self.url = ""
        self.calls: list[str] = []

    def navigate(self, url: str) -> list[Element]:
        self.url = url
        self.calls.append("navigate")
        return [Element("e1", "textbox", "Email"), Element("e3", "button", "Send")]

    def read(self) -> list[Element]:
        self.calls.append("read")
        return [Element("e1", "textbox", "Email")]

    def click(self, ref: str) -> list[Element]:
        self.calls.append(f"click:{ref}")
        return [Element("e1", "textbox", "Email")]

    def type_text(self, ref: str, text: str) -> list[Element]:
        self.calls.append(f"type:{ref}")
        return [Element("e1", "textbox", "Email")]

    def page_html(self) -> str:
        return LOGIN_FORM if self.url.endswith("/login") else "<p>Contact us</p>"

    def page_text(self) -> str:
        return "Contact us"

    def frame(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.scrape import ssrf

    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.216.34"])


def _stack(*, authority: str, approve: bool) -> tuple[LedgeredTool, _Page, list[Any], BrowserTool]:
    ledger = TaintLedger(authority=authority)
    ledger.set_instruction(f"Open {PAGE} and send the form with my email")
    page = _Page()
    browser = BrowserTool(driver=page, situation=BrowserSituation())
    asked: list[Any] = []

    def person(*args: Any) -> bool:
        asked.append(args)
        return approve

    return LedgeredTool(browser, ledger, narrow_on_taint=True, approve=person), page, asked, browser


def test_there_are_four_distinct_classes_in_the_kernels_vocabulary() -> None:
    assert [row.cls for row in CONFIRMATIONS] == list(ConfirmationClass)
    assert {row.decision for row in CONFIRMATIONS} <= set(Decision)
    for row in CONFIRMATIONS:
        assert row.browser and row.mandate and row.requires and row.released_by


def test_every_gate_a_row_names_exists() -> None:
    for row in CONFIRMATIONS:
        for pointer in row.enforced_by:
            module_name, _, attrs = pointer.partition(":")
            obj: Any = importlib.import_module(module_name)
            for part in attrs.split("."):
                obj = getattr(obj, part)  # raises when the row names a gate that is gone


def test_hand_over_no_approver_can_release_it() -> None:
    row = confirmation(ConfirmationClass.HAND_OVER)
    assert row.decision is Decision.BLOCK and row.released_by == "nobody"
    tool, page, asked, browser = _stack(authority="provenance", approve=True)
    tool.run(action="navigate", url=PAGE.replace("/contact", "/login"))
    tool.run(action="type", ref="e2", text="the-owners-password")
    assert asked, "the taint gate asked, and the person said yes"
    assert "type:e2" not in page.calls, "yet nothing was typed into the password field"
    assert browser.situation is not None and browser.situation.take_handover() is not None


def test_confirm_at_action_time_asks_even_when_the_request_named_the_page() -> None:
    row = confirmation(ConfirmationClass.CONFIRM_AT_ACTION)
    assert row.decision is Decision.REVIEW and row.released_by == "a person, at the call"
    tool, page, asked, _ = _stack(authority="provenance", approve=False)
    tool.run(action="navigate", url=PAGE)
    out = tool.run(action="click", ref="e3")
    assert len(asked) == 1 and is_refusal(out) and "click:e3" not in page.calls


def test_pre_approved_by_the_request_asks_nobody_when_the_request_named_the_page() -> None:
    row = confirmation(ConfirmationClass.PRE_APPROVED_BY_REQUEST)
    assert row.decision is Decision.REVIEW and row.released_by == "the request"
    tool, page, asked, _ = _stack(authority="authority", approve=False)
    tool.run(action="navigate", url=PAGE)
    tool.run(action="click", ref="e3")
    assert asked == [] and "click:e3" in page.calls


def test_none_reading_the_loaded_page_asks_nobody() -> None:
    row = confirmation(ConfirmationClass.NONE)
    assert row.decision is Decision.ALLOW
    tool, page, asked, _ = _stack(authority="provenance", approve=False)
    tool.run(action="navigate", url=PAGE)
    tool.run(action="read")
    assert asked == [] and "read" in page.calls
