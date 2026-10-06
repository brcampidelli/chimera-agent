"""Tests for the update-available version check — OFFLINE (the GitHub fetch is monkeypatched).

No test hits the real network: every case stubs ``version_api._fetch_latest`` (or forces it to raise)
and resets the module-level cache so results don't leak between cases.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

import chimera
import chimera.api.version_api as version_api


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    """Reset the module-level GitHub cache before AND after each test (it persists across the process)."""
    version_api._cache.clear()
    yield
    version_api._cache.clear()


# --- pure version comparison -----------------------------------------------------------------------


def test_parse_version_parses_clean_tuples() -> None:
    # Five elements now, not three: the fourth orders a final release above its candidates and the
    # fifth is the candidate number. See `test_a_prerelease_hears_about_updates.py`.
    assert version_api._parse_version("0.30.0") == (0, 30, 0, 1, 0)
    assert version_api._parse_version("1.2.3") == (1, 2, 3, 1, 0)


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
        version_api, "_fetch_latest", lambda **_: ("0.31.0", "https://example.test/releases/0.31.0")
    )
    out = version_api.check_version()
    assert out["version"] == "0.30.0"
    assert out["latest"] == "0.31.0"
    assert out["update_available"] is True
    assert out["notes_url"] == "https://example.test/releases/0.31.0"


def test_update_not_available_when_equal(monkeypatch: Any) -> None:
    monkeypatch.setattr(chimera, "__version__", "0.31.0")
    monkeypatch.setattr(version_api, "_fetch_latest", lambda **_: ("0.31.0", "https://example.test/r"))
    out = version_api.check_version()
    assert out["update_available"] is False
    assert out["notes_url"] is None  # nothing to link to when there's no update


def test_update_not_available_when_local_is_newer(monkeypatch: Any) -> None:
    monkeypatch.setattr(chimera, "__version__", "0.32.0")
    monkeypatch.setattr(version_api, "_fetch_latest", lambda **_: ("0.31.0", "https://example.test/r"))
    out = version_api.check_version()
    assert out["update_available"] is False
    assert out["latest"] == "0.31.0"  # honestly reported, just not an "update"


def test_fetch_failure_degrades_to_null_latest(monkeypatch: Any) -> None:
    # A fetch failure (mocked here as a return of (None, None)) → no update signal, a clean dict.
    monkeypatch.setattr(chimera, "__version__", "0.30.0")
    monkeypatch.setattr(version_api, "_fetch_latest", lambda **_: (None, None))
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

    def _counted(**_: object) -> tuple[str | None, str | None]:
        calls["n"] += 1
        return "0.31.0", "https://example.test/r"

    monkeypatch.setattr(version_api, "_fetch_latest", _counted)
    version_api.check_version()
    version_api.check_version()
    version_api.check_version()
    assert calls["n"] == 1  # fetched once, served from cache after


class _Resp(io.BytesIO):
    """A minimal stand-in for urlopen's context-managed response."""

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


def _stub_urlopen(monkeypatch: Any, payload: object, seen: dict[str, Any] | None = None) -> None:
    """Stub urlopen to serve ``payload``, RECORDING the request it was handed into ``seen``.

    Recording matters: a stub with a ``*args, **kwargs`` signature that ignores what it is given
    cannot tell a correctly-built request from a broken one, so every assertion downstream of it
    passes for the wrong reason.
    """

    def _urlopen(req: Any = None, timeout: Any = None) -> Any:
        if seen is not None:
            seen["url"] = req.full_url
            seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
            seen["timeout"] = timeout
        return _Resp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(version_api.urllib.request, "urlopen", _urlopen)


def test_fetch_latest_strips_leading_v_and_reads_html_url(monkeypatch: Any) -> None:
    # Stub urlopen to return a canned GitHub payload — the tag's leading "v" is stripped, html_url kept.
    payload = {"tag_name": "v0.31.0", "html_url": "https://github.com/x/releases/tag/v0.31.0"}
    _stub_urlopen(monkeypatch, payload)
    assert version_api._fetch_latest() == (
        "0.31.0",
        "https://github.com/x/releases/tag/v0.31.0",
    )


def test_fetch_latest_strips_an_uppercase_v_tag(monkeypatch: Any) -> None:
    # GitHub tags are conventionally "v0.31.0", but a "V0.31.0" release must not report as "V0.31.0"
    # (which would fail to parse, silently suppressing a real update).
    _stub_urlopen(monkeypatch, {"tag_name": "V1.2.3", "html_url": "https://e.test/r"})
    assert version_api._fetch_latest() == ("1.2.3", "https://e.test/r")


def test_fetch_latest_builds_the_github_request_with_headers_and_a_timeout(monkeypatch: Any) -> None:
    """The REQUEST itself is asserted, not just the parsed reply.

    GitHub's API rejects a request with no User-Agent, and a missing timeout would let a blocked
    network stall the version check indefinitely. Neither failure is visible in the return value, so
    only inspecting the outgoing request can catch them.
    """
    seen: dict[str, Any] = {}
    _stub_urlopen(monkeypatch, {"tag_name": "v9.9.9", "html_url": "https://e.test/r"}, seen)

    assert version_api._fetch_latest() == ("9.9.9", "https://e.test/r")
    assert seen["url"] == version_api._RELEASES_LATEST_URL
    # urllib capitalises header keys on the way in, so `seen["headers"]` is lower-cased for comparison.
    assert seen["headers"]["user-agent"] == version_api._USER_AGENT
    assert seen["headers"]["accept"] == "application/vnd.github+json"
    assert seen["timeout"] == version_api._TIMEOUT


def test_fetch_latest_rejects_a_blank_or_non_string_tag(monkeypatch: Any) -> None:
    # A whitespace/absent/non-string tag is unusable. It must degrade to a clean (None, None) — NOT
    # to an empty `latest` paired with a live notes_url, which would read as a real release.
    _stub_urlopen(monkeypatch, {"tag_name": "   ", "html_url": "https://e.test/r"})
    assert version_api._fetch_latest() == (None, None)

    _stub_urlopen(monkeypatch, {"tag_name": 5, "html_url": "https://e.test/r"})
    assert version_api._fetch_latest() == (None, None)

    _stub_urlopen(monkeypatch, {"html_url": "https://e.test/r"})  # tag_name missing entirely
    assert version_api._fetch_latest() == (None, None)


def test_fetch_latest_treats_a_blank_html_url_as_no_notes_url(monkeypatch: Any) -> None:
    # An empty html_url is not a link. It must come back as None so check_version falls back to the
    # releases page rather than emitting an empty href.
    _stub_urlopen(monkeypatch, {"tag_name": "v1.0.0", "html_url": ""})
    assert version_api._fetch_latest() == ("1.0.0", None)


def test_fetch_latest_returns_none_for_a_non_dict_payload(monkeypatch: Any) -> None:
    _stub_urlopen(monkeypatch, ["not", "a", "dict"])
    assert version_api._fetch_latest() == (None, None)


def test_cached_latest_serves_from_cache_then_refetches_once_the_ttl_expires(
    monkeypatch: Any,
) -> None:
    """The TTL boundary, on a frozen clock.

    The cache is what stops repeated GETs hammering GitHub's API, so "it refetched" and "it went
    stale forever" are both real bugs. A frozen clock pins the boundary exactly: still cached at
    TTL-1s, refetched AT the TTL.
    """
    clock = {"t": 10_000.0}
    monkeypatch.setattr(version_api.time, "monotonic", lambda: clock["t"])
    calls = {"n": 0}

    def _fetch(**_: object) -> tuple[str | None, str | None]:
        calls["n"] += 1
        return f"0.{calls['n']}.0", "https://e.test/r"

    monkeypatch.setattr(version_api, "_fetch_latest", _fetch)

    assert version_api._cached_latest() == ("0.1.0", "https://e.test/r")
    assert calls["n"] == 1

    clock["t"] = 10_000.0 + version_api._TTL - 1  # inside the TTL → served from cache
    assert version_api._cached_latest() == ("0.1.0", "https://e.test/r")
    assert calls["n"] == 1

    clock["t"] = 10_000.0 + version_api._TTL  # AT the TTL → expired, refetched
    assert version_api._cached_latest() == ("0.2.0", "https://e.test/r")
    assert calls["n"] == 2


# --- the 2026-10-05 mutation gate: the candidate (prerelease) half ------------------------------
# Every test above drives the STABLE track. The prereleases branch — the LIST endpoint, the draft
# skip, the newest-by-parsed-version choice — had no test at all, which is why its mutants all
# survived. The parse/prerelease predicates below are the same story.


def test_parse_version_understands_release_candidates() -> None:
    assert version_api._parse_version("0.48.0rc46") == (0, 48, 0, 0, 46)
    assert version_api._parse_version("0.48.0rc1") == (0, 48, 0, 0, 1)


def test_a_candidate_orders_below_its_own_final_release() -> None:
    """The fourth element is what orders them: rc46 < 0.48.0, and rc45 < rc46."""
    assert version_api._parse_version("0.48.0rc46") < version_api._parse_version("0.48.0")
    assert version_api._parse_version("0.48.0rc45") < version_api._parse_version("0.48.0rc46")


def test_is_prerelease_says_what_it_sees() -> None:
    assert version_api._is_prerelease("0.48.0rc1") is True
    assert version_api._is_prerelease("0.48.0") is False
    assert version_api._is_prerelease("not-a-version") is False  # unparseable is NOT a candidate


def test_fetch_latest_prereleases_uses_the_list_endpoint_and_picks_the_newest(
    monkeypatch: Any,
) -> None:
    """The candidate track reads the LIST (the only endpoint that returns candidates) and picks by
    PARSED VERSION, not by the list's order — GitHub sorts by date, and a patch cut after a
    candidate would otherwise be announced as the newer of the two."""
    seen: dict[str, Any] = {}
    _stub_urlopen(
        monkeypatch,
        [
            {"tag_name": "v0.48.0rc45", "html_url": "https://e.test/rc45"},
            {"tag_name": "v0.48.0", "html_url": "https://e.test/final"},
            {"tag_name": "v0.48.0rc46", "html_url": "https://e.test/rc46"},
        ],
        seen,
    )
    assert version_api._fetch_latest(include_prereleases=True) == ("0.48.0", "https://e.test/final")
    assert seen["url"] == version_api._RELEASES_LIST_URL


def test_fetch_latest_prereleases_skips_drafts_and_unparseable_tags(monkeypatch: Any) -> None:
    """A draft is not a release anyone can install; an unparseable tag is not a version anyone
    can compare. Neither may win — and a draft that parses HIGHER must not leak through."""
    _stub_urlopen(
        monkeypatch,
        [
            {"tag_name": "v0.49.0rc1", "html_url": "https://e.test/draft", "draft": True},
            {"tag_name": "nonsense", "html_url": "https://e.test/x"},
            {"tag_name": "v0.48.0rc9", "html_url": "https://e.test/rc9"},
        ],
    )
    assert version_api._fetch_latest(include_prereleases=True) == (
        "0.48.0rc9",
        "https://e.test/rc9",
    )


def test_fetch_latest_prereleases_tolerates_non_dict_entries(monkeypatch: Any) -> None:
    """The list endpoint can hand back non-objects; they are skipped, not crashed on."""
    _stub_urlopen(monkeypatch, ["junk", {"tag_name": "v1.2.3", "html_url": "https://e.test/r"}])
    assert version_api._fetch_latest(include_prereleases=True) == ("1.2.3", "https://e.test/r")


def test_fetch_latest_prereleases_returns_none_for_a_non_list_payload(monkeypatch: Any) -> None:
    _stub_urlopen(monkeypatch, {"oops": True})
    assert version_api._fetch_latest(include_prereleases=True) == (None, None)


def test_fetch_latest_prereleases_breaks_ties_by_first_seen(monkeypatch: Any) -> None:
    """Two releases with the SAME parsed version: the first seen wins. A `>=` there would let the
    later one overwrite it — the notes link would point at whichever came last in the list."""
    _stub_urlopen(
        monkeypatch,
        [
            {"tag_name": "v1.0.0", "html_url": "https://e.test/first"},
            {"tag_name": "v1.0.0", "html_url": "https://e.test/second"},
        ],
    )
    assert version_api._fetch_latest(include_prereleases=True) == ("1.0.0", "https://e.test/first")


def test_cached_latest_default_asks_for_the_stable_track(monkeypatch: Any) -> None:
    """The default question is the STABLE track. A flipped default would serve every stable
    install the candidate list — the exact mix-up the two cache slots exist to prevent."""
    seen: dict[str, Any] = {}

    def _fetch(**kwargs: object) -> tuple[str | None, str | None]:
        seen.update(kwargs)
        return ("0.31.0", "https://e.test/r")

    monkeypatch.setattr(version_api, "_fetch_latest", _fetch)
    version_api._cached_latest()
    assert seen == {"include_prereleases": False}


def test_check_version_asks_the_track_the_running_version_is_on(monkeypatch: Any) -> None:
    """A candidate hears about candidates; a stable does not. The kwarg IS the contract — someone
    running 0.47.0 never opted into a prerelease, and answering their check with one would push
    them onto a track they did not choose."""
    seen: dict[str, Any] = {}

    def _fetch(**kwargs: object) -> tuple[str | None, str | None]:
        seen.update(kwargs)
        return (None, None)

    monkeypatch.setattr(version_api, "_fetch_latest", _fetch)

    monkeypatch.setattr(chimera, "__version__", "0.48.0rc46")
    version_api.check_version()
    assert seen == {"include_prereleases": True}

    seen.clear()
    monkeypatch.setattr(chimera, "__version__", "0.48.0")
    version_api.check_version()
    assert seen == {"include_prereleases": False}
