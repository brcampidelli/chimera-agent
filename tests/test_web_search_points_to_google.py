"""When every key provider is dead, `web_search` names the browser's Google search, if it is there.

Owner decision (26/10/2026): Brave is out of credit, so searches go through Google in the owner's
visible Chrome. The agent must not keep retrying a provider the cooldown will refuse; the message
says to use `browser action=google_search`, and only when that action is actually in this registry.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chimera.config import Settings
from chimera.tools import web
from chimera.tools.web import WebSearchTool

_HINT = "Use browser action=google_search instead."
_KEY = "fake-brave-key-for-test"
_CREDIT = {"error": {"code": "CREDIT_EXHAUSTED"}}


class _Response:
    def __init__(self, payload: Any, *, status: int = 200) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self._payload = payload

    def json(self) -> Any:
        return self._payload


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ("TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY",
                 "CHIMERA_WEBSEARCH_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    web._COOLDOWNS.clear()
    web._REFUSED_UNTIL.clear()
    yield
    web._COOLDOWNS.clear()
    web._REFUSED_UNTIL.clear()


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    aliases = {str(Settings.model_fields[name].validation_alias): value for name, value in values.items()}
    settings = Settings(_env_file=None, **aliases)  # type: ignore[call-arg]  # aliases are runtime-only init kwargs
    monkeypatch.setattr(web, "get_settings", lambda: settings)


def _brave_out_of_credit(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        return _Response(_CREDIT, status=402)

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_out_of_credit_points_to_google_when_the_browser_offers_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, brave_api_key=_KEY, websearch_provider="auto")
    _brave_out_of_credit(monkeypatch)
    tool = WebSearchTool()
    tool.google_fallback = lambda: True

    answer = tool.run(query="needle")

    assert answer.startswith("error: no web search provider could answer (brave: ")
    assert answer.endswith(_HINT)


def test_no_hint_when_the_browser_is_not_in_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, brave_api_key=_KEY, websearch_provider="auto")
    _brave_out_of_credit(monkeypatch)
    tool = WebSearchTool()
    tool.google_fallback = lambda: False

    assert _HINT not in tool.run(query="needle")


def test_no_hint_without_a_fallback_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, brave_api_key=_KEY, websearch_provider="auto")
    _brave_out_of_credit(monkeypatch)

    assert _HINT not in WebSearchTool().run(query="needle")


def test_cooldown_after_refusal_still_points_to_google_and_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, brave_api_key=_KEY, websearch_provider="auto")
    calls = _brave_out_of_credit(monkeypatch)
    tool = WebSearchTool()
    tool.google_fallback = lambda: True
    tool.run(query="first")

    answer = tool.run(query="second")

    assert "in cooldown" in answer
    assert answer.endswith(_HINT)
    assert len(calls) == 1


def test_missing_keys_point_to_google_when_the_browser_offers_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, websearch_provider="auto")
    tool = WebSearchTool()
    tool.google_fallback = lambda: True

    answer = tool.run(query="needle")

    assert "web_search is unavailable" in answer
    assert answer.endswith(_HINT)


def test_missing_keys_without_google_say_nothing_about_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, websearch_provider="auto")
    tool = WebSearchTool()
    tool.google_fallback = lambda: False

    answer = tool.run(query="needle")

    assert "web_search is unavailable" in answer
    assert _HINT not in answer


def test_a_rate_limited_provider_alongside_a_refused_one_is_not_a_dead_end(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, brave_api_key=_KEY, tavily_api_key="fake-tv", websearch_provider="auto")
    _brave_out_of_credit(monkeypatch)

    def fake_post(url: str, **kwargs: Any) -> _Response:
        return _Response({}, status=429)

    monkeypatch.setattr(httpx, "post", fake_post)
    tool = WebSearchTool()
    tool.google_fallback = lambda: True

    answer = tool.run(query="needle")

    assert _HINT not in answer
