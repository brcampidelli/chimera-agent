"""The web search adapters normalize provider responses and never bypass fetch governance.

The previous search tool was hard-wired to Tavily, so a machine with the already-used Firecrawl
credential still had no search capability. These tests pin each wire adapter separately from
selection policy: a change in one provider's response envelope must not silently corrupt the common
(title, URL, snippet) output or send a provider key to another service.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chimera.config import Settings
from chimera.tools import web
from chimera.tools.web import WebSearchTool


class _Response:
    def __init__(self, payload: dict[str, Any], *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture(autouse=True)
def _no_cooldown_survives_a_test(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The cooldowns are process-wide on purpose (every session shares a provider's 429), so a test
    that leaves one behind would decide the next test's provider. Search keys are cleared too: the
    shared conftest leaves search credentials alone, and one exported on a developer's machine would
    turn "no key" tests into tests of that key."""
    for name in ("TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY",
                 "CHIMERA_WEBSEARCH_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    web._COOLDOWNS.clear()
    yield
    web._COOLDOWNS.clear()


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    # Settings reads its env aliases (`TAVILY_API_KEY`), not field names, and never the developer's
    # `.env` here: a key on this machine would otherwise decide which provider a test exercises.
    aliases = {str(Settings.model_fields[name].validation_alias): value for name, value in values.items()}
    settings = Settings(_env_file=None, **aliases)  # type: ignore[call-arg]  # aliases are runtime-only init kwargs
    monkeypatch.setattr(web, "get_settings", lambda: settings)
    web._COOLDOWNS.clear()


@pytest.mark.parametrize(
    ("provider", "payload", "method"),
    [
        ("tavily", {"results": [{"title": "Title", "url": "https://example.test", "content": "Tavily text"}]}, "post"),
        ("firecrawl", {"data": [{"title": "Title", "url": "https://example.test", "description": "Firecrawl text"}]}, "post"),
        ("exa", {"results": [{"title": "Title", "url": "https://example.test", "text": "Exa text"}]}, "post"),
        ("brave", {"web": {"results": [{"title": "Title", "url": "https://example.test", "description": "Brave text"}]}}, "get"),
    ],
)
def test_each_search_provider_normalizes_http_response(
    monkeypatch: pytest.MonkeyPatch, provider: str, payload: dict[str, Any], method: str,
) -> None:
    _settings(monkeypatch, **{f"{provider}_api_key": f"{provider}-secret", "websearch_provider": provider})
    captured: list[tuple[str, dict[str, Any]]] = []

    def fake_request(url: str, **kwargs: Any) -> _Response:
        captured.append((url, kwargs))
        return _Response(payload)

    monkeypatch.setattr(httpx, method, fake_request)
    answer = WebSearchTool().run(query="needle", max_results=3)

    assert "Title — https://example.test" in answer
    assert f"{provider.capitalize()} text" in answer
    assert f"provider: {provider}" in answer
    assert captured and captured[0][1].get("timeout") == 30.0
    assert f"{provider}-secret" not in answer


def test_auto_fails_over_on_429_and_skips_provider_during_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, tavily_api_key="fake-tv", firecrawl_api_key="fake-fc")
    monkeypatch.setattr(web.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(web.time, "time", lambda: 1_800_000_000.0)
    calls: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        if url == web._TAVILY_URL:
            return _Response({}, status=429, headers={"Retry-After": "120"})
        return _Response({"data": [{"title": "Fallback", "url": "https://example.test", "description": "found"}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    tool = WebSearchTool()

    first = tool.run(query="needle")
    second = tool.run(query="needle")

    assert "provider: firecrawl" in first and "provider: firecrawl" in second
    assert calls == [web._TAVILY_URL, web._FIRECRAWL_URL, web._FIRECRAWL_URL]
    assert web._COOLDOWNS["tavily"] == 220.0


def test_auto_uses_sixty_second_default_for_missing_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, tavily_api_key="fake-tv", firecrawl_api_key="fake-fc")
    monkeypatch.setattr(web.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(web.time, "time", lambda: 1_800_000_000.0)

    def fake_post(url: str, **kwargs: Any) -> _Response:
        if url == web._TAVILY_URL:
            return _Response({}, status=429)
        return _Response({"data": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    WebSearchTool().run(query="needle")

    assert web._COOLDOWNS["tavily"] == 70.0


def test_fixed_provider_does_not_fail_over_and_does_not_leak_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, tavily_api_key="fake-tv", firecrawl_api_key="fake-fc", websearch_provider="tavily")
    calls: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        return _Response({}, status=429, headers={"Retry-After": "45"})

    monkeypatch.setattr(httpx, "post", fake_post)
    answer = WebSearchTool().run(query="needle")

    assert calls == [web._TAVILY_URL]
    assert "rate limited" in answer
    assert "fake-tv" not in answer
    assert "fake-fc" not in answer
    assert "provider: firecrawl" not in answer


def test_no_keys_reports_all_supported_key_names(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)

    answer = WebSearchTool().run(query="needle")

    assert "unavailable" in answer
    for key in ("TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"):
        assert key in answer


def test_auto_skips_a_provider_already_in_cooldown_and_tries_the_next_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, tavily_api_key="fake-tv", exa_api_key="fake-exa")
    monkeypatch.setattr(web.time, "monotonic", lambda: 99.0)
    web._COOLDOWNS["tavily"] = 100.0
    calls: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        return _Response({"results": [{"title": "Exa hit", "url": "https://example.test", "text": "text"}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    answer = WebSearchTool().run(query="needle")

    assert "provider: exa" in answer
    assert calls == [web._EXA_URL]


def test_all_configured_providers_in_cooldown_returns_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, tavily_api_key="fake-tv", brave_api_key="fake-br")
    monkeypatch.setattr(web.time, "monotonic", lambda: 50.0)
    web._COOLDOWNS.update({"tavily": 100.0, "brave": 100.0})

    answer = WebSearchTool().run(query="needle")

    assert "all configured web search providers are in cooldown" in answer


def test_provider_http_failure_text_cannot_echo_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, tavily_api_key="top-secret")

    def fail_post(url: str, **kwargs: Any) -> _Response:
        raise httpx.ConnectError("https://api.tavily.com/?key=top-secret")

    monkeypatch.setattr(httpx, "post", fail_post)
    answer = WebSearchTool().run(query="needle")

    assert answer == "error: tavily search failed (ConnectError)."
    assert "top-secret" not in answer


def test_search_tool_remains_classified_as_a_public_fetch() -> None:
    from chimera.governance.ledger import FETCH_TOOLS

    assert WebSearchTool.name in FETCH_TOOLS


def test_default_registry_accepts_each_provider_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from chimera.config import get_settings
    from chimera.tools.builtin import default_registry

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for env in ("TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"):
        for other in ("TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"):
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(env, "provider-secret")
        get_settings.cache_clear()
        assert "web_search" in default_registry(tmp_path / "ws").names(), env
    get_settings.cache_clear()


def test_supported_search_credentials_are_editable_and_masked() -> None:
    from chimera.api.config_api import ALLOWED_KEYS, read_config

    expected = {"TAVILY_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"}
    assert expected <= ALLOWED_KEYS
    configured = Settings(  # type: ignore[call-arg]  # aliases are runtime-only init kwargs
        _env_file=None, TAVILY_API_KEY="fake-tv", FIRECRAWL_API_KEY="fake-fc",
        EXA_API_KEY="fake-exa", BRAVE_API_KEY="fake-brave",
    )
    listed = {entry["env"]: entry for entry in read_config(configured)["providers"]}
    assert expected <= listed.keys()
    assert all(listed[key]["set"] for key in expected)
    assert all("secret" not in str(listed[key]["hint"]) for key in expected)
