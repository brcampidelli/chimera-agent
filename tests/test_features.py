"""Tests for the optional-features catalog and the web_search reference tool."""

from __future__ import annotations

import pytest

from chimera.config import Settings
from chimera.features import feature_status


def _by_name(settings: Settings) -> dict[str, object]:
    return {status.feature.name: status for status in feature_status(settings)}


def test_builtin_features_are_always_ready() -> None:
    statuses = _by_name(Settings())
    assert statuses["vision"].ready is True  # type: ignore[attr-defined]
    assert statuses["pet"].ready is True  # type: ignore[attr-defined]
    assert statuses["deliverable"].ready is True  # type: ignore[attr-defined]


def test_web_search_ready_only_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    blocked = _by_name(Settings())["web_search"]
    assert blocked.ready is False  # type: ignore[attr-defined]
    assert "TAVILY_API_KEY" in blocked.blocker  # type: ignore[attr-defined]

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    ready = _by_name(Settings())["web_search"]
    assert ready.ready is True  # type: ignore[attr-defined]


def test_browser_reports_missing_dependency() -> None:
    import importlib.util

    if importlib.util.find_spec("playwright") is not None:
        pytest.skip("browser extra installed — this checks the missing-dependency report")
    status = _by_name(Settings())["browser"]
    # playwright isn't installed in the test environment
    assert status.ready is False  # type: ignore[attr-defined]
    assert "playwright" in status.blocker  # type: ignore[attr-defined]


def test_web_search_tool_without_key_returns_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    from chimera.config import get_settings
    from chimera.tools.web import WebSearchTool

    get_settings.cache_clear()
    assert "TAVILY_API_KEY" in WebSearchTool().run(query="anything")
