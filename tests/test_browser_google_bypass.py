"""Google search under the conversation's "Ignore permissions" switch: no card, everything else kept.

Owner decision (26/10/2026): with the switch on, the agent does things directly except a block, a
secret leaving, or a rewrite of main. A search in the owner's visible Chrome is none of those, so the
card goes. Pacing, the daily and turn limits, the CAPTCHA hand-back, headless refusal and the audit
record all still apply, and the switch is read at each search, so turning it on mid-turn counts.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chimera.governance.ledger_tool import FENCE_OPEN
from chimera.tools.browser import BrowserTool
from tests.test_browser_google_search import make_tool


class _AuditLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def record(self, event: str, data: dict[str, object]) -> None:
        self.records.append((event, data))


def test_bypass_on_searches_without_the_card_and_audits_bypass(tmp_path: Path) -> None:
    tool, driver, _clock = make_tool(tmp_path)
    asked: list[object] = []
    tool.ask_outside = lambda question: asked.append(question) or True
    audit = _AuditLog()
    tool.audit = audit
    tool.bypass_permissions = True
    output = tool.run(action="google_search", query="cats")
    assert "Example result" in output and FENCE_OPEN in output
    assert asked == []
    assert len(driver.urls) == 1
    assert audit.records[-1] == (
        "browser.google_search",
        {"query": "cats", "result": "ok", "approved_by": "bypass"},
    )


def test_bypass_off_asks_on_every_search_as_before(tmp_path: Path) -> None:
    tool, driver, clock = make_tool(tmp_path)
    asked: list[object] = []
    tool.ask_outside = lambda question: asked.append(question) or True
    tool.bypass_permissions = False
    for idx in range(2):
        assert "Example result" in tool.run(action="google_search", query=str(idx))
        clock.now += 25
    assert len(asked) == 2
    assert len(driver.urls) == 2


def test_bypass_switched_on_mid_turn_is_honoured_on_the_next_call(tmp_path: Path) -> None:
    tool, driver, clock = make_tool(tmp_path)
    asked: list[object] = []
    tool.ask_outside = lambda question: asked.append(question) or True
    audit = _AuditLog()
    tool.audit = audit
    switch = {"on": False}
    tool.bypass_permissions = lambda: switch["on"]
    assert "Example result" in tool.run(action="google_search", query="first")
    assert len(asked) == 1
    switch["on"] = True
    clock.now += 25
    assert "Example result" in tool.run(action="google_search", query="second")
    assert len(asked) == 1
    assert audit.records[-1][1]["approved_by"] == "bypass"
    switch["on"] = False
    clock.now += 25
    assert "Example result" in tool.run(action="google_search", query="third")
    assert len(asked) == 2
    assert audit.records[-1][1]["approved_by"] == "owner"
    assert len(driver.urls) == 3


def test_bypass_keeps_the_turn_limit_and_the_pacing(tmp_path: Path) -> None:
    tool, driver, clock = make_tool(tmp_path)
    asked: list[object] = []
    tool.ask_outside = lambda question: asked.append(question) or True
    tool.bypass_permissions = True
    for idx in range(5):
        assert "Example result" in tool.run(action="google_search", query=str(idx))
        clock.now += 25
    assert "turn limit is 5" in tool.run(action="google_search", query="sixth")
    assert len(driver.urls) == 5
    assert asked == []

    paced, paced_driver, paced_clock = make_tool(tmp_path / "paced")
    paced.bypass_permissions = True
    paced.run(action="google_search", query="first")
    paced.run(action="google_search", query="second")
    assert paced_clock.waits == [20]
    assert len(paced_driver.urls) == 2


def test_bypass_keeps_the_daily_limit(tmp_path: Path) -> None:
    tool, driver, clock = make_tool(tmp_path)
    tool.bypass_permissions = True
    today = datetime.fromtimestamp(clock(), UTC).date().isoformat()
    tool.google_budget_path.parent.mkdir(parents=True, exist_ok=True)
    tool.google_budget_path.write_text(
        json.dumps({"date": today, "count": 30, "last_search": None, "blocked_until": None}),
        encoding="utf-8",
    )
    assert "daily limit is 30" in tool.run(action="google_search", query="x")
    assert driver.urls == []


def test_bypass_keeps_the_captcha_stop_and_the_thirty_minute_block(tmp_path: Path) -> None:
    tool, driver, clock = make_tool(tmp_path)
    tool.bypass_permissions = True
    driver.text = "recaptcha"
    assert "Google is asking for a human check" in tool.run(action="google_search", query="x")
    assert driver.calls == ["navigate", "page_text"]
    assert "human check" in tool.run(action="google_search", query="again")
    assert driver.calls == ["navigate", "page_text"]
    budget = json.loads(tool.google_budget_path.read_text(encoding="utf-8"))
    assert budget["blocked_until"] == clock() + 30 * 60


def test_bypass_still_refuses_headless_and_google_off(tmp_path: Path) -> None:
    headless, headless_driver, _clock = make_tool(tmp_path / "headless", engine="playwright")
    headless.bypass_permissions = True
    assert "only runs in your own Chrome" in headless.run(action="google_search", query="x")
    off, off_driver, _clock = make_tool(tmp_path / "off", mode="off")
    off.bypass_permissions = True
    assert "only runs in your own Chrome" in off.run(action="google_search", query="x")
    assert headless_driver.calls == [] and off_driver.calls == []


def test_bypass_does_not_replace_the_approver_being_wired(tmp_path: Path) -> None:
    tool, driver, _clock = make_tool(tmp_path)
    tool.ask_outside = None
    tool.bypass_permissions = True
    assert "nobody here can ask" in tool.run(action="google_search", query="x")
    assert driver.calls == []


def test_offers_google_search_only_for_the_paired_chrome_with_ask(tmp_path: Path) -> None:
    chrome, _driver, _clock = make_tool(tmp_path / "chrome")
    assert chrome.offers_google_search() is True
    off, _driver, _clock = make_tool(tmp_path / "off", mode="off")
    assert off.offers_google_search() is False
    headless, _driver, _clock = make_tool(tmp_path / "pw", engine="playwright")
    assert headless.offers_google_search() is False


def test_registry_hands_the_browser_the_live_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings

    class Link:
        @staticmethod
        def connected() -> bool:
            return True

        @staticmethod
        def transport() -> object:
            return object()

    class Sink:
        def __init__(self) -> None:
            self.cards: list[object] = []

        def emit(self, question: object) -> None:
            self.cards.append(question)

        def __call__(self, question: object) -> None:
            self.emit(question)

    settings = Settings.model_validate(
        {
            "CHIMERA_HOME": str(tmp_path / "home"),
            "CHIMERA_BROWSER_GOOGLE": "ask",
            "CHIMERA_APPROVAL_MODE": "allow",
        }
    )
    monkeypatch.setattr(
        "chimera.governance.pending.ask_durably",
        lambda _home, action, reason, **kwargs: (
            kwargs["on_asked"](
                type("Question", (), {"action": action, "reason": reason})()
            ) or False
        ),
    )
    ws = tmp_path / "workspace"
    ws.mkdir()
    switch = {"on": False}
    registry, _ledger = assemble_registry(
        CodeSeams(), ws, settings, object(), steps=1, surface="api:turn",
        approval_sink=Sink(), browser_allowed=True, browser_link=Link(),
        bypass_permissions=lambda: switch["on"],
    )
    wrapped = registry.get("browser")
    while not isinstance(wrapped, BrowserTool):
        wrapped = getattr(wrapped, "inner", None) or getattr(wrapped, "_inner", None)
        assert wrapped is not None
    assert wrapped.offers_google_search() is True
    assert wrapped._google_bypass_on() is False
    switch["on"] = True
    assert wrapped._google_bypass_on() is True

