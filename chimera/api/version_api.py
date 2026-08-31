"""``GET /api/version`` logic: report the running version and, only when GitHub CONFIRMS a strictly-
newer release, signal that an update is available.

Honest-by-construction: any failure (offline, timeout, rate-limit, malformed JSON) degrades to
``latest=None`` / ``update_available=False`` — it can NEVER surface a false "update available". The
GitHub result is cached in a module-level variable with a TTL so repeated GETs don't hammer the API.

PRIVACY: this is a plain GET of GitHub's PUBLIC releases API — no user data is sent.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("api.version")

# The releases API (newest release) + the human releases page fallback for the "notes" link.
_RELEASES_LATEST_URL = "https://api.github.com/repos/brcampidelli/chimera-agent/releases/latest"
#: The LIST, which is the only endpoint that returns prereleases — `/releases/latest` excludes
#: them by GitHub's definition, which is the other half of why an app on a candidate was told
#: about nothing: even with a parser that understood the version, the newest thing this could
#: ever report was the last stable.
_RELEASES_LIST_URL = (
    "https://api.github.com/repos/brcampidelli/chimera-agent/releases?per_page=30"
)
_RELEASES_PAGE_URL = "https://github.com/brcampidelli/chimera-agent/releases"
_USER_AGENT = "chimera-agent"  # GitHub's API rejects requests without a User-Agent
_TIMEOUT = 4.0  # a short timeout — a slow/blocked network must not stall the version check
_TTL = 3600.0  # cache the GitHub result for an hour so repeated GETs don't hammer the API

# Module-level cache, keyed on whether prereleases were asked for — the two questions have
# different answers and sharing one slot would serve a stable install a candidate.
# Both successes and failures are cached, so a transient error won't trigger a burst of retries.
_cache: dict[bool, tuple[float, tuple[str | None, str | None]]] = {}


#: ``0.48.0`` or ``0.48.0rc46`` — the only two shapes `scripts/cut_release.py` will produce. It
#: refuses post and dev releases by name, so nothing else has to be understood here.
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?$")


def _parse_version(text: str) -> tuple[int, ...] | None:
    """Parse a version into a sortable tuple; ``None`` when it doesn't parse.

    Pre-releases are understood now, and until they were, an app on a release candidate was told
    about nothing at all: ``0.48.0rc46`` failed to parse, so every comparison returned False — not
    a newer candidate, not even the final ``0.48.0``. With forty-six candidates in this series that
    is the common case rather than the edge.

    The fourth element is what orders them: ``1`` for a final release and ``0`` for a candidate, so
    ``0.48.0rc46 < 0.48.0`` while ``0.48.0rc45 < 0.48.0rc46``. Anything else — a source-tree marker
    like ``0.0.0+source``, a post release, anything unrecognised — still returns None and still
    yields ``update_available=False``. Never a false positive is the rule that has not changed.

    Written here rather than taken from `packaging`, which is not a declared dependency of this
    project and is present only transitively: a version check that breaks a clean install is worse
    than one that understands two shapes.
    """
    m = _VERSION.match(text.strip())
    if m is None:
        return None
    maior, menor, patch, rc = m.groups()
    if rc is None:
        return (int(maior), int(menor), int(patch), 1, 0)
    return (int(maior), int(menor), int(patch), 0, int(rc))


def _is_prerelease(text: str) -> bool:
    """Whether this version is a release candidate. Unparseable is NOT a prerelease: a source
    checkout must not be offered candidates it never opted into."""
    parsed = _parse_version(text)
    return parsed is not None and parsed[3] == 0


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


def _tag_and_url(entrada: Any) -> tuple[str | None, str | None]:
    """One release object -> ``(tag_without_leading_v, html_url)``, or ``(None, None)``."""
    if not isinstance(entrada, dict):
        return None, None
    tag = entrada.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return None, None
    tag = tag.strip()
    latest = tag[1:] if tag[:1] in ("v", "V") else tag  # strip a single leading "v" (e.g. v0.31.0)
    html_url = entrada.get("html_url")
    return latest or None, (html_url if isinstance(html_url, str) and html_url else None)


def _get_json(url: str) -> Any:
    """A fixed GitHub API GET, or ``None`` on ANY failure.

    Fail-silent by design: network, timeout, rate-limit, non-JSON — all of it becomes "no update
    signal" rather than an exception, because a version check must never be the reason the app
    stops working.
    """
    req = urllib.request.Request(  # noqa: S310 — a fixed https GitHub API URL, not user input
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — fixed https URL
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — fail-silent: no update signal, never a raise
        _log.debug("version check failed: %s", exc)
        return None


def _fetch_latest(*, include_prereleases: bool = False) -> tuple[str | None, str | None]:
    """The newest release ``(tag_without_leading_v, html_url)`` on the asked-for track.

    Without prereleases this is `/releases/latest`, one cheap request, and GitHub itself does the
    excluding. With them it has to be the LIST, because that endpoint is the only one that returns
    a candidate at all.

    The newest is chosen by PARSED VERSION rather than by the list's own order: GitHub sorts by
    creation date, and a patch cut after a candidate would otherwise be announced as the newer of
    the two. Drafts and unparseable tags are skipped rather than guessed at.
    """
    if not include_prereleases:
        return _tag_and_url(_get_json(_RELEASES_LATEST_URL))

    payload = _get_json(_RELEASES_LIST_URL)
    if not isinstance(payload, list):
        return None, None
    melhor: tuple[tuple[int, ...], str, str | None] | None = None
    for entrada in payload:
        if isinstance(entrada, dict) and entrada.get("draft"):
            continue
        tag, url = _tag_and_url(entrada)
        parsed = _parse_version(tag or "")
        if tag is None or parsed is None:
            continue
        if melhor is None or parsed > melhor[0]:
            melhor = (parsed, tag, url)
    return (melhor[1], melhor[2]) if melhor else (None, None)


def _cached_latest(*, include_prereleases: bool = False) -> tuple[str | None, str | None]:
    """Return the (possibly cached) latest-release ``(latest, notes_url)``, refetching past the TTL."""
    now = time.monotonic()
    guardado = _cache.get(include_prereleases)
    if guardado is not None and now - guardado[0] < _TTL:
        return guardado[1]
    result = _fetch_latest(include_prereleases=include_prereleases)
    _cache[include_prereleases] = (now, result)
    return result


def check_version() -> dict[str, Any]:
    """Build the ``VersionOut`` dict. Blocking (does a cached GitHub GET) — run on an executor thread.

    ``update_available`` is True ONLY when a strictly-newer release is confirmed; ``notes_url`` is the
    release page for that update, else ``None``. On any fetch failure it degrades to the current version
    with ``latest=None`` — an honest "no update available", never a false signal.
    """
    import chimera

    current = chimera.__version__
    # A candidate is offered candidates; a stable release is not. Someone running 0.47.0 never
    # opted into a prerelease, and answering their update check with one would push them onto a
    # track they did not choose — the same reason `/releases/latest` excludes them by default.
    latest, html_url = _cached_latest(include_prereleases=_is_prerelease(current))
    available = bool(latest) and _is_newer(latest or "", current)
    return {
        "version": current,
        "latest": latest,
        "update_available": available,
        # Prefer the specific release's page; fall back to the releases listing. Only when an update
        # is actually available — otherwise there is nothing to link to.
        "notes_url": (html_url or _RELEASES_PAGE_URL) if available else None,
    }
