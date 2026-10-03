"""Study 29, P5.2 review: the app's chat asks before its browser opens a site off the list.

The Settings hint says "a page off the list asks you first", and the plan lists the chat among the
surfaces where it becomes a question. Only the Code screen handed the browser an approver
(`code_api`, ``ask_outside``); in the app's chat — the main surface, with a person at it — the same
navigation was refused with "nobody here can approve it". The chat already has an approver and a
card for governance (`_owner_allows` + the session's announcer); the browser now gets that one.

Driven through the factory `chimera app` really hands `build_api_app`, as `test_the_app_chat_can_ask`
does and for its reason: a check that only asks whether an attribute was set passes in the world
where the question is never drawn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.governance.pending import PendingApproval
from chimera.scrape import ssrf
from chimera.tools.browser import BrowserTool, Element


def _app_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    from typer.testing import CliRunner

    import chimera.api as api_pkg
    import chimera.cli.main as cli
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    captured: dict[str, Any] = {}

    def fake_build_api_app(factory: Any, **kwargs: Any) -> Any:
        captured["factory"] = factory
        raise SystemExit(0)

    monkeypatch.setattr(api_pkg, "build_api_app", fake_build_api_app)
    CliRunner().invoke(cli.app, ["app", "--workspace", str(tmp_path / "ws"), "--no-open"])
    try:
        assert "factory" in captured, "the command never reached build_api_app"
        return captured["factory"]()
    finally:
        get_settings.cache_clear()


def _browser(session: Any) -> BrowserTool:
    for tool in session.agent.tools.tools():
        while not isinstance(tool, BrowserTool) and getattr(tool, "inner", None) is not None:
            tool = tool.inner
        if isinstance(tool, BrowserTool):
            return tool
    raise AssertionError("the chat has no browser")


class _Driver:
    def __init__(self) -> None:
        self.visited: list[str] = []

    def navigate(self, url: str) -> list[Element]:
        self.visited.append(url)
        return [Element("e1", "link", "home")]

    def read(self) -> list[Element]:
        return []

    def page_html(self) -> str:
        return "<p>x</p>"

    def page_text(self) -> str:
        return "x"

    def frame(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture()
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.215.14"])


def test_a_site_off_the_list_is_a_question_on_the_chat_and_a_yes_opens_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    from chimera.governance.pending import answer

    home = tmp_path / "home"
    session = _app_chat(
        tmp_path,
        monkeypatch,
        CHIMERA_BROWSER_SITES="example.com",
        CHIMERA_GUARD_CHAT="1",
        CHIMERA_APPROVAL_MODE="ask",
        CHIMERA_APPROVAL_WAIT="20",
    )
    drawn: list[PendingApproval] = []

    def screen(question: PendingApproval) -> None:
        drawn.append(question)
        answer(home, question.id, True)  # what `POST /api/approvals/{id}` does

    session.approval_sink.emit = screen
    browser = _browser(session)
    driver = _Driver()
    browser._driver = driver  # type: ignore[assignment]  # a real Chromium is not this test's business

    out = browser.run(action="navigate", url="https://news.other.example/")
    assert len(drawn) == 1, "the question never reached the chat's screen"
    assert "news.other.example" in drawn[0].reason
    assert not out.startswith("error:"), out
    assert driver.visited == ["https://news.other.example/"]


def test_a_no_on_the_chat_refuses_and_the_driver_is_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    from chimera.governance.pending import answer

    home = tmp_path / "home"
    session = _app_chat(
        tmp_path,
        monkeypatch,
        CHIMERA_BROWSER_SITES="example.com",
        CHIMERA_GUARD_CHAT="1",
        CHIMERA_APPROVAL_MODE="ask",
        CHIMERA_APPROVAL_WAIT="20",
    )
    session.approval_sink.emit = lambda question: answer(home, question.id, False)
    browser = _browser(session)
    driver = _Driver()
    browser._driver = driver  # type: ignore[assignment]

    out = browser.run(action="navigate", url="https://news.other.example/")
    assert out.startswith("error:") and "a person was asked and refused" in out
    assert driver.visited == []
