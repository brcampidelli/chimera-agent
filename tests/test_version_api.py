"""Tests for the update-available version check — OFFLINE (the GitHub fetch is monkeypatched).

No test hits the real network: every case stubs ``version_api._fetch_latest`` (or forces it to raise)
and resets the module-level cache so results don't leak between cases.
"""

from __future__ import annotations

from typing import Any

import pytest

import chimera
import chimera.api.version_api as version_api


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    """Reset the module-level GitHub cache before AND after each test (it persists across the process)."""
    version_api._cache = None
    yield
    version_api._cache = None


# --- pure version comparison -----------------------------------------------------------------------


def test_parse_version_parses_clean_tuples() -> None:
    assert version_api._parse_version("0.30.0") == (0, 30, 0)
    assert version_api._parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_rejects_non_numeric() -> None:
    # A source-tree marker / pre-release suffix doesn't parse — the honest "can't compare".
    assert version_api._parse_version("0.0.0+source") is None
    assert version_api._parse_version("v0.30.0") is None  # the leading "v" is stripped upstream
    assert version_api._parse_version("") is None


def test_is_newer_compares_as_int_tuples_not_strings() -> None:
    # The whole point: 0.10.0 must be newer than 0.9.0 (string compare would get this wrong).
    assert version_api._is_newer("0.10.0", "0.9.0") is True
    assert version_api._is_newer("0.9.0", "0.10.0") is False
    assert version_api._is_newer("0.30.0", "0.30.0") is False  # equal is NOT newer
    assert version_api._is_newer("1.0.0", "0.99.99") is True


def test_is_newer_false_when_either_side_unparseable() -> None:
    assert version_api._is_newer("not-a-version", "0.30.0") is False
    assert version_api._is_newer("0.31.0", "0.0.0+source") is False


# --- check_version() end to end (fetch stubbed) ----------------------------------------------------


def test_update_available_true_when_github_is_newer(monkeypatch: Any) -> None:
    monkeypatch.setattr(chimera, "__version__", "0.30.0")
    monkeypatch.setattr(
        version_api, "_fetch_latest", lambda: ("0.31.0", "https://example.test/releases/0.31.0")
    )
    out = version_api.check_version()
    assert out["version"] == "0.30.0"
    assert out["latest"] == "0.31.0"
    assert out["update_available"] is True
    assert out["notes_url"] == "https://example.test/releases/0.31.0"


def test_update_not_available_when_equal(monkeypatch: Any) -> None:
    monkeypatch.setattr(chimera, "__version__", "0.31.0")
    monkeypatch.setattr(version_api, "_fetch_latest", lambda: ("0.31.0", "https://example.test/r"))
    out = version_api.check_version()
    assert out["update_available"] is False
    assert out["notes_url"] is None  # nothing to link to when there's no update


def test_update_not_available_when_local_is_newer(monkeypatch: Any) -> None:
    monkeypatch.setattr(chimera, "__version__", "0.32.0")
    monkeypatch.setattr(version_api, "_fetch_latest", lambda: ("0.31.0", "https://example.test/r"))
    out = version_api.check_version()
    assert out["update_available"] is False
    assert out["latest"] == "0.31.0"  # honestly reported, just not an "update"


def test_fetch_failure_degrades_to_null_latest(monkeypatch: Any) -> None:
    # A fetch failure (mocked here as a return of (None, None)) → no update signal, a clean dict.
    monkeypatch.setattr(chimera, "__version__", "0.30.0")
    monkeypatch.setattr(version_api, "_fetch_latest", lambda: (None, None))
    out = version_api.check_version()
    assert out == {
        "version": "0.30.0",
        "latest": None,
        "update_available": False,
        "notes_url": None,
    }


def test_fetch_that_raises_is_swallowed_by_fetch_latest(monkeypatch: Any) -> None:
    # The REAL _fetch_latest must never raise: force urlopen to blow up and assert (None, None).
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(version_api.urllib.request, "urlopen", _boom)
    assert version_api._fetch_latest() == (None, None)


def test_check_version_caches_and_does_not_refetch(monkeypatch: Any) -> None:
    # The cache means repeated calls hit GitHub once, not every time.
    monkeypatch.setattr(chimera, "__version__", "0.30.0")
    calls = {"n": 0}

    def _counted() -> tuple[str | None, str | None]:
        calls["n"] += 1
        return "0.31.0", "https://example.test/r"

    monkeypatch.setattr(version_api, "_fetch_latest", _counted)
    version_api.check_version()
    version_api.check_version()
    version_api.check_version()
    assert calls["n"] == 1  # fetched once, served from cache after


def test_fetch_latest_strips_leading_v_and_reads_html_url(monkeypatch: Any) -> None:
    # Stub urlopen to return a canned GitHub payload — the tag's leading "v" is stripped, html_url kept.
    import io
    import json

    class _Resp(io.BytesIO):
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    payload = {"tag_name": "v0.31.0", "html_url": "https://github.com/x/releases/tag/v0.31.0"}
    monkeypatch.setattr(
        version_api.urllib.request,
        "urlopen",
        lambda *a, **k: _Resp(json.dumps(payload).encode("utf-8")),
    )
    assert version_api._fetch_latest() == (
        "0.31.0",
        "https://github.com/x/releases/tag/v0.31.0",
    )
