"""Google search policy and pacing using a fake visible Chrome driver."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from chimera.governance.ledger import FETCH_TOOLS
from chimera.governance.ledger_tool import FENCE_CLOSE, FENCE_OPEN
from chimera.tools.browser import BrowserTool, Element


class FakeChrome:
    def __init__(self, text: str = "Search results") -> None:
        self.text = text
        self.calls: list[str] = []
        self.urls: list[str] = []
        self.current = ""
        self.text_by_page: str | None = None
        self.redirect_url: str | None = None
        self.navigate_error: str | None = None

    def navigate(self, url: str) -> list[Element]:
        self.calls.append("navigate")
        if self.navigate_error is not None:
            raise RuntimeError(self.navigate_error)
        self.urls.append(url)
        self.current = self.redirect_url or url
        return []

    def page_text(self) -> str:
        self.calls.append("page_text")
        return self.text_by_page or self.text

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
        tool, driver, clock = make_tool(tmp_path / phrase.replace("/", "_"), driver=FakeChrome())
        if phrase == "/sorry/index":
            driver.redirect_url = "https://www.google.com/sorry/index"
        else:
            driver.text_by_page = phrase
        response = tool.run(action="google_search", query="x")
        assert "Google is asking for a human check" in response
        assert driver.calls == ["navigate", "page_text"]
        assert "human check" in tool.run(action="google_search", query="again")
        assert driver.calls == ["navigate", "page_text"]
        budget = json.loads(tool.google_budget_path.read_text(encoding="utf-8"))
        assert budget["blocked_until"] == clock() + 30 * 60


def test_turn_limit_counts_searches_after_twenty_five_seconds(tmp_path: Path) -> None:
    tool, driver, clock = make_tool(tmp_path)
    asked: list[str] = []
    tool.ask_outside = lambda _question: asked.append("asked") or True
    for idx in range(5):
        assert "Example result" in tool.run(action="google_search", query=str(idx))
        clock.now += 25
    assert "turn limit is 5" in tool.run(action="google_search", query="sixth")
    assert len(driver.urls) == 5
    assert len(asked) == 5


def test_google_approval_precedes_rate_limit_wait(tmp_path: Path) -> None:
    tool, _driver, _clock = make_tool(tmp_path)
    events: list[str] = []
    tool.ask_outside = lambda _question: events.append("asked") or True
    tool._sleeper = lambda _seconds: events.append("waited")
    tool.run(action="google_search", query="first")
    events.clear()
    tool.run(action="google_search", query="second")
    assert events == ["asked", "waited"]


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
        daily._google_turn_searches = 0
        daily_clock.now += 20
        daily.run(action="google_search", query=str(idx))
    daily._google_turn_searches = 0
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


@pytest.mark.parametrize("phrase", ["tráfego incomum", "não sou um robô"])
def test_portuguese_challenge_markers_block(tmp_path: Path, phrase: str) -> None:
    tool, _driver, _clock = make_tool(tmp_path, driver=FakeChrome(phrase))
    assert "human check" in tool.run(action="google_search", query="x")


def test_consent_and_final_sorry_url_block_even_without_challenge_text(tmp_path: Path) -> None:
    tool, driver, _clock = make_tool(tmp_path, driver=FakeChrome())
    driver.redirect_url = "https://consent.google.com/m"
    assert "human check" in tool.run(action="google_search", query="consent")

    isolated, driver, _clock = make_tool(tmp_path / "sorry", driver=FakeChrome())
    driver.redirect_url = "https://www.google.com/sorry/index"
    assert "human check" in isolated.run(action="google_search", query="sorry")


def test_challenge_word_in_later_page_text_or_result_does_not_block(tmp_path: Path) -> None:
    page = "ordinary results\n" * 40 + "recaptcha"
    tool, driver, _clock = make_tool(tmp_path, driver=FakeChrome(page))
    assert "Example result" in tool.run(action="google_search", query="recaptcha")
    assert "google_results" in driver.calls


def test_ungranted_redirect_origin_blocks_for_thirty_minutes(tmp_path: Path) -> None:
    driver = FakeChrome()
    driver.navigate_error = "This site has not been granted to the Chimera extension"
    tool, _driver, clock = make_tool(tmp_path, driver=driver)
    records: list[tuple[str, dict[str, object]]] = []

    class Audit:
        def record(self, event: str, data: dict[str, object]) -> None:
            records.append((event, data))

    tool.audit = Audit()
    assert "redirected to a human check" in tool.run(action="google_search", query="redirect")
    assert records[-1][1]["result"] == "captcha"
    budget = json.loads(tool.google_budget_path.read_text(encoding="utf-8"))
    assert budget["blocked_until"] == clock() + 30 * 60
    assert "human check" in tool.run(action="google_search", query="again")
    assert driver.calls == ["navigate"]


def test_audit_records_outcome_and_truncated_query_for_each_decision(tmp_path: Path) -> None:
    class Audit:
        def __init__(self) -> None:
            self.records: list[tuple[str, dict[str, object]]] = []

        def record(self, event: str, data: dict[str, object]) -> None:
            self.records.append((event, data))

    cases = ("ok", "refused", "captcha", "budget")
    for result in cases:
        tool, driver, _clock = make_tool(tmp_path / result)
        audit = Audit()
        tool.audit = audit
        query = "q" * 240
        if result == "refused":
            tool.ask_outside = lambda _question: False
        elif result == "captcha":
            driver.text = "recaptcha"
        elif result == "budget":
            tool._google_turn_searches = 5
        tool.run(action="google_search", query=query)
        assert audit.records[-1] == (
            "browser.google_search",
            {
                "query": query[:200],
                "result": result,
                "approved_by": False if result == "refused" else None if result == "budget" else "owner",
            },
        )


def test_registry_always_asks_for_google_even_when_mode_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.tools.browser import BrowserTool

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

    sink = Sink()
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
    registry, _ledger = assemble_registry(
        CodeSeams(), ws, settings, object(), steps=1, surface="api:turn",
        approval_sink=sink, browser_allowed=True, browser_link=Link(),
    )
    wrapped = registry.get("browser")
    while not isinstance(wrapped, BrowserTool):
        wrapped = getattr(wrapped, "inner", None) or getattr(wrapped, "_inner", None)
        assert wrapped is not None
    response = wrapped.run(action="google_search", query="approval required")
    assert "asked and refused" in response
    assert sink.cards
    assert getattr(sink.cards[0], "action", "") == "google search: approval required"


def test_registry_google_question_caps_card_query_at_two_hundred_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.tools.browser import BrowserTool

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

    sink = Sink()
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
    registry, _ledger = assemble_registry(
        CodeSeams(), ws, settings, object(), steps=1, surface="api:turn",
        approval_sink=sink, browser_allowed=True, browser_link=Link(),
    )
    wrapped = registry.get("browser")
    while not isinstance(wrapped, BrowserTool):
        wrapped = getattr(wrapped, "inner", None) or getattr(wrapped, "_inner", None)
        assert wrapped is not None
    long_query = "q" * 240
    wrapped.run(action="google_search", query=long_query)
    assert sink.cards
    question = sink.cards[0]
    assert getattr(question, "action", "") == f"google search: {long_query[:200]}"


def test_playwright_has_no_schema_action_and_refuses(tmp_path: Path) -> None:
    tool, _driver, _clock = make_tool(tmp_path, engine="playwright")
    assert "google_search" not in tool.parameters["properties"]["action"]["enum"]
    assert "only runs in your own Chrome" in tool.run(action="google_search", query="x")
