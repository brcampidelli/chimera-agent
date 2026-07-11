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


def test_browser_is_builtin_ready() -> None:
    # Playwright is a CORE dependency now (the Chromium binary auto-installs on first use),
    # so the browser capability is always available.
    status = _by_name(Settings())["browser"]
    assert status.ready is True  # type: ignore[attr-defined]


def test_multimedia_features_are_in_the_catalog() -> None:
    names = set(_by_name(Settings()))
    # The features the [full] extra enables must be discoverable via `chimera features`.
    for name in ("documents", "media_download", "speech_to_text", "data_analysis", "charts"):
        assert name in names, f"{name} missing from the features catalog"


def test_missing_dep_reports_extra_based_install_hint() -> None:
    # A blocked feature with an `extra` gives a copy-pasteable pip hint, not a bare module name.
    from chimera.features import Feature, FeatureStatus

    st = FeatureStatus(
        feature=Feature("documents", "…", dep="markitdown", extra="documents"),
        has_key=True, has_dep=False,
    )
    assert st.ready is False
    assert st.blocker == "pip install 'chimera-agent[documents]'"


def test_missing_system_binary_reports_install() -> None:
    from chimera.features import Feature, FeatureStatus

    st = FeatureStatus(
        feature=Feature("media_download", "…", dep="yt_dlp", extra="media-dl", bin="ffmpeg"),
        has_key=True, has_dep=True, has_bin=False,
    )
    assert st.ready is False
    assert "ffmpeg" in st.blocker


def test_catalog_deps_use_importable_names() -> None:
    # Regression: a PIP name with a hyphen ("youtube-transcript-api") never resolves via find_spec.
    from chimera.features import CATALOG

    for feature in CATALOG:
        if feature.dep is not None:
            assert "-" not in feature.dep, f"{feature.name}: dep must be an import name, not a PIP name"
            assert feature.dep.isidentifier() or "." in feature.dep


def test_web_search_tool_without_key_returns_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    from chimera.config import get_settings
    from chimera.tools.web import WebSearchTool

    get_settings.cache_clear()
    assert "TAVILY_API_KEY" in WebSearchTool().run(query="anything")
