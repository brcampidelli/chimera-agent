"""Google search policy and pacing using a fake visible Chrome driver."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from chimera.governance.ledger import FETCH_TOOLS
from chimera.governance.ledger_tool import FENCE_CLOSE, FENCE_OPEN
from chimera.tools.browser import BrowserTool, Element


class FakeChrome:
    def __init__(self, text: str = "Search results") -> None:
        self.text = text
        self.calls: list[str] = []
        self.urls: list[str] = []
        self.current = ""

    def navigate(self, url: str) -> list[Element]:
        self.calls.append("navigate")
        self.urls.append(url)
        self.current = url
        return []

    def page_text(self) -> str:
        self.calls.append("page_text")
        return self.text

    def current_url(self) -> str:
        return self.current

    def google_results(self) -> list[dict[str, str]]:
        self.calls.append("google_results")
        return [{"title": "Example result", "link": "https://example.com/", "snippet": "A snippet"}]

    def read(self) -> list[Element]:
        return []

    def click(self, ref: str) -> list[Element]:
        return []

    def type_text(self, ref: str, text: str) -> list[Element]:
        return []

    def back(self) -> list[Element]:
        return []

    def page_html(self) -> str:
        return ""

    def screenshot(self, path: str) -> None:
        pass

    def frame(self) -> None:
        return None

    def close(self) -> None:
        pass


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now
        self.waits: list[float] = []
    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


def make_tool(
    tmp_path: Path,
    *,
    mode: str = "ask",
    engine: str = "chrome",
    driver: FakeChrome | None = None,
    ask=None,
    clock: Clock | None = None,
) -> tuple[BrowserTool, FakeChrome, Clock]:
    fake = driver or FakeChrome()
    timer = clock or Clock()
    tool = BrowserTool(
        driver=fake,
        engine=engine,
        google_search=mode,
        google_budget_path=tmp_path / "browser" / "google_budget.json",
        ask_outside=ask or (lambda _question: True),
        clock=timer,
        sleeper=timer.sleep,
    )
    return tool, fake, timer


def test_google_search_off_is_absent_and_refuses(tmp_path: Path) -> None:
    tool, _driver, _clock = make_tool(tmp_path, mode="off")
    assert "google_search" not in tool.parameters["properties"]["action"]["enum"]
    assert "only runs in your own Chrome" in tool.run(action="google_search", query="q")


def test_ask_approval_returns_fenced_results_and_taints(tmp_path: Path) -> None:
    tool, driver, _clock = make_tool(tmp_path)
    output = tool.run(action="google_search", query="cats & dogs")
    assert FENCE_OPEN in output and FENCE_CLOSE in output
    assert "Example result" in output and "https://example.com/" in output
    parsed = urlparse(driver.urls[0])
    assert parsed.scheme == "https" and parsed.netloc == "www.google.com" and parsed.path == "/search"
    assert parse_qs(parsed.query) == {"q": ["cats & dogs"], "hl": ["pt-BR"]}
    assert "browser" in FETCH_TOOLS


def test_ask_without_approver_or_owner_refusal(tmp_path: Path) -> None:
    tool, _driver, _clock = make_tool(tmp_path)
    tool.ask_outside = None
    assert "nobody here can ask" in tool.run(action="google_search", query="x")
    refused, driver, _clock = make_tool(tmp_path / "refused", ask=lambda _question: False)
    assert "a person was asked and refused. Do not retry." in refused.run(action="google_search", query="x")
    assert driver.calls == []


def test_captcha_stops_and_persists_block_for_thirty_minutes(tmp_path: Path) -> None:
    for phrase in ("/sorry/index", "recaptcha"):
        tool, driver, clock = make_tool(tmp_path / phrase.replace("/", "_"), driver=FakeChrome(phrase))
        response = tool.run(action="google_search", query="x")
        assert "Google is asking for a human check" in response
        assert driver.calls == ["navigate", "page_text"]
        assert "human check" in tool.run(action="google_search", query="again")
        assert driver.calls == ["navigate", "page_text"]
        budget = json.loads(tool.google_budget_path.read_text(encoding="utf-8"))
        assert budget["blocked_until"] == clock() + 30 * 60


def test_limits_waits_daily_turn_and_utc_reset(tmp_path: Path) -> None:
    tool, driver, clock = make_tool(tmp_path)
    assert "Example result" in tool.run(action="google_search", query="first")
    assert "Example result" in tool.run(action="google_search", query="second")
    assert clock.waits == [20]
    for idx in range(3):
        clock.now += 20
        tool.run(action="google_search", query=str(idx))
    assert "turn limit is 5" in tool.run(action="google_search", query="sixth")
    assert len(driver.urls) == 5

    daily, daily_driver, daily_clock = make_tool(tmp_path / "daily")
    for idx in range(30):
        daily_clock.now += 20
        daily.run(action="google_search", query=str(idx))
    assert "daily limit is 30" in daily.run(action="google_search", query="31")
    assert len(daily_driver.urls) == 30

    reset, _reset_driver, reset_clock = make_tool(tmp_path / "reset")
    reset.google_budget_path.parent.mkdir(parents=True)
    reset.google_budget_path.write_text(
        json.dumps({"date": "2000-01-01", "count": 30, "last_search": None, "blocked_until": None}),
        encoding="utf-8",
    )
    reset_clock.now = datetime(2030, 1, 2, tzinfo=UTC).timestamp()
    assert "Example result" in reset.run(action="google_search", query="new day")
    assert json.loads(reset.google_budget_path.read_text(encoding="utf-8"))["count"] == 1


def test_playwright_has_no_schema_action_and_refuses(tmp_path: Path) -> None:
    tool, _driver, _clock = make_tool(tmp_path, engine="playwright")
    assert "google_search" not in tool.parameters["properties"]["action"]["enum"]
    assert "only runs in your own Chrome" in tool.run(action="google_search", query="x")
