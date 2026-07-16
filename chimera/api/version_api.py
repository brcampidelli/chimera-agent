"""``GET /api/version`` logic: report the running version and, only when GitHub CONFIRMS a strictly-
newer release, signal that an update is available.

Honest-by-construction: any failure (offline, timeout, rate-limit, malformed JSON) degrades to
``latest=None`` / ``update_available=False`` — it can NEVER surface a false "update available". The
GitHub result is cached in a module-level variable with a TTL so repeated GETs don't hammer the API.

PRIVACY: this is a plain GET of GitHub's PUBLIC releases API — no user data is sent.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("api.version")

# The releases API (newest release) + the human releases page fallback for the "notes" link.
_RELEASES_LATEST_URL = "https://api.github.com/repos/brcampidelli/chimera-agent/releases/latest"
_RELEASES_PAGE_URL = "https://github.com/brcampidelli/chimera-agent/releases"
_USER_AGENT = "chimera-agent"  # GitHub's API rejects requests without a User-Agent
_TIMEOUT = 4.0  # a short timeout — a slow/blocked network must not stall the version check
_TTL = 3600.0  # cache the GitHub result for an hour so repeated GETs don't hammer the API

# Module-level cache: (fetched_at_monotonic, (latest, notes_url)). ``None`` until the first fetch.
# Both successes and failures are cached, so a transient error won't trigger a burst of retries.
_cache: tuple[float, tuple[str | None, str | None]] | None = None


def _parse_version(text: str) -> tuple[int, ...] | None:
    """Parse ``MAJOR.MINOR.PATCH`` into an int tuple for comparison; ``None`` when it doesn't parse.

    Strict: every dot-separated segment must be a non-negative integer (a source-tree marker like
    ``0.0.0+source`` or any pre-release suffix fails to parse and is treated as "can't compare" — which
    yields ``update_available=False``, never a false positive).
    """
    parts = text.split(".")
    if not parts:
        return None
    out: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        out.append(int(part))
    return tuple(out)


def _is_newer(latest: str, current: str) -> bool:
    """True only when ``latest`` parses to a STRICTLY greater version tuple than ``current``.

    If EITHER string doesn't parse to a clean int tuple, return False — the honest default (we never
    claim an update on an unparseable version).
    """
    latest_v = _parse_version(latest)
    current_v = _parse_version(current)
    if latest_v is None or current_v is None:
        return False
    return latest_v > current_v


def _fetch_latest() -> tuple[str | None, str | None]:
    """Fetch the newest release ``(tag_without_leading_v, html_url)`` from GitHub.

    Fail-silent: ANY error (network, timeout, rate-limit, non-JSON, missing keys) returns
    ``(None, None)`` — it never raises. The caller turns that into "no update available".
    """
    req = urllib.request.Request(  # noqa: S310 — a fixed https GitHub API URL, not user input
        _RELEASES_LATEST_URL,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — fixed https URL
            payload: Any = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — fail-silent: no update signal, never a raise
        _log.debug("version check failed: %s", exc)
        return None, None
    if not isinstance(payload, dict):
        return None, None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return None, None
    tag = tag.strip()
    latest = tag[1:] if tag[:1] in ("v", "V") else tag  # strip a single leading "v" (e.g. v0.31.0)
    html_url = payload.get("html_url")
    notes_url = html_url if isinstance(html_url, str) and html_url else None
    return latest or None, notes_url


def _cached_latest() -> tuple[str | None, str | None]:
    """Return the (possibly cached) latest-release ``(latest, notes_url)``, refetching past the TTL."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL:
        return _cache[1]
    result = _fetch_latest()
    _cache = (now, result)
    return result


def check_version() -> dict[str, Any]:
    """Build the ``VersionOut`` dict. Blocking (does a cached GitHub GET) — run on an executor thread.

    ``update_available`` is True ONLY when a strictly-newer release is confirmed; ``notes_url`` is the
    release page for that update, else ``None``. On any fetch failure it degrades to the current version
    with ``latest=None`` — an honest "no update available", never a false signal.
    """
    import chimera

    current = chimera.__version__
    latest, html_url = _cached_latest()
    available = bool(latest) and _is_newer(latest or "", current)
    return {
        "version": current,
        "latest": latest,
        "update_available": available,
        # Prefer the specific release's page; fall back to the releases listing. Only when an update
        # is actually available — otherwise there is nothing to link to.
        "notes_url": (html_url or _RELEASES_PAGE_URL) if available else None,
    }
