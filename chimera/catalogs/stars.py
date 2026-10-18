"""GitHub stars for catalogue items, read on this machine with the date of the reading (plan M4).

A star count is the only popularity number Chimera shows, because it is the only one somebody else
measured: Chimera itself counts no installs. So the number travels with ``stars_at`` (when it was
read) and ``stars_repo`` (whose stars they are — often a whole upstream repository holding many
items), and a missing number is ``null``, never zero.

The rules, each a test in ``tests/test_catalog_sections_and_stars.py``:

* **Never on the request path.** The route serves what the cache holds and, when something is
  missing or older than :data:`STARS_TTL_S`, starts one background refresh. The first read after a
  fresh install shows the stars the catalogue itself recorded at the pinned commit (with their date),
  or no stars at all; a later read shows this machine's reading.
* **Bounded.** One refresh asks for at most :data:`REFRESH_BATCH` repositories, one request each,
  single attempt, through the plugin installer's allowlisted fetch (HTTPS, ``api.github.com`` only,
  a timeout, ``GITHUB_TOKEN`` when set). The first failure ends the refresh, and no refresh starts
  again for :data:`FAILURE_BACKOFF_S` — the anonymous API allows 60 requests an hour, and a machine
  offline should not spend its afternoon timing out.
* **Only names, never URLs, come from the catalogue.** A repository is fetched only when it is a
  plain ``owner/repo`` (both parts GitHub-shaped); the URL is built here.
* **Off switch.** ``CHIMERA_CATALOG_STARS=0`` stops every fetch; the cache is still read, and items
  keep whatever the pinned catalogue recorded.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chimera.catalogs.model import CatalogOut
from chimera.telemetry import get_logger

log = get_logger(__name__)

#: A reading younger than this is not asked for again.
STARS_TTL_S = 24 * 60 * 60
#: Repositories asked for in one refresh. The catalogues name ~40 distinct repositories today, so
#: two refreshes cover them, and one never spends more than a third of the anonymous hourly ceiling.
REFRESH_BATCH = 20
#: After a failed request (offline, throttled, refused), no refresh starts again for this long.
FAILURE_BACKOFF_S = 15 * 60
#: One repository's metadata is a few KB; anything past this is not a repository response.
_MAX_RESPONSE = 256 * 1024

_PART = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
_REPO = re.compile(rf"^{_PART}/{_PART}$")

#: ``(url, limit) -> bytes``; raises on any failure.
Fetcher = Callable[[str, int], bytes]


@dataclass(frozen=True)
class StarReading:
    stars: int
    read_at: str


@dataclass
class _State:
    readings: dict[str, StarReading]
    #: repo -> when GitHub answered 404 for it. Not asked again within the TTL, so one renamed or
    #: deleted repository does not stop every refresh at the same place forever.
    misses: dict[str, str]
    last_failure: str | None


def cache_path(home: Path) -> Path:
    return Path(home) / "catalogs" / "stars.json"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _read(home: Path) -> _State:
    try:
        raw = json.loads(cache_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _State({}, {}, None)
    if not isinstance(raw, dict):
        return _State({}, {}, None)
    readings: dict[str, StarReading] = {}
    repos = raw.get("repos")
    for repo, row in (repos.items() if isinstance(repos, dict) else []):
        if not (isinstance(repo, str) and _REPO.match(repo) and isinstance(row, dict)):
            continue
        stars, read_at = row.get("stars"), row.get("read_at")
        if isinstance(stars, int) and stars >= 0 and isinstance(read_at, str) and _parse(read_at):
            readings[repo] = StarReading(stars, read_at)
    misses_raw = raw.get("misses")
    misses = {
        repo: at for repo, at in (misses_raw.items() if isinstance(misses_raw, dict) else [])
        if isinstance(repo, str) and _REPO.match(repo) and isinstance(at, str) and _parse(at)
    }
    failure = raw.get("last_failure")
    return _State(readings, misses, failure if isinstance(failure, str) else None)


def _write(home: Path, state: _State) -> None:
    path = cache_path(home)
    payload = {
        "format": 1,
        "repos": {repo: {"stars": r.stars, "read_at": r.read_at}
                  for repo, r in sorted(state.readings.items())},
        "misses": dict(sorted(state.misses.items())),
        "last_failure": state.last_failure,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="stars.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        log.warning("could not write the stars cache at %s: %s", path, exc)


def cached_stars(home: Path) -> dict[str, StarReading]:
    """Every reading this machine has, whatever its age — each carries its own date."""
    return _read(home).readings


def _github_get(url: str, limit: int) -> bytes:
    from chimera.skills import bundles

    # One attempt: a refresh that fails is retried by the next one, after the back-off, and the
    # retrying fetch would sleep through throttles inside a thread nobody is waiting on.
    return bundles._get_once(url, limit=limit)


def due(home: Path, repos: Iterable[str], *, now: datetime | None = None) -> list[str]:
    """The repositories with no reading younger than :data:`STARS_TTL_S`, in a stable order."""
    moment = now or _now()
    state = _read(home)
    ttl = timedelta(seconds=STARS_TTL_S)
    out: list[str] = []
    for repo in sorted({r for r in repos if r and _REPO.match(r)}):
        reading = state.readings.get(repo)
        read_at = _parse(reading.read_at) if reading else None
        missed_at = _parse(state.misses.get(repo))
        fresh = (read_at is not None and moment - read_at < ttl) or (
            missed_at is not None and moment - missed_at < ttl)
        if not fresh:
            out.append(repo)
    return out


def refresh(
    home: Path,
    repos: Iterable[str],
    *,
    get: Fetcher | None = None,
    now: datetime | None = None,
    batch: int = REFRESH_BATCH,
) -> int:
    """Read the stars of up to ``batch`` due repositories. Returns how many readings were stored.

    Synchronous; the route calls :func:`schedule_refresh`, which runs this on a thread.
    """
    moment = now or _now()
    state = _read(home)
    failed_at = _parse(state.last_failure)
    if failed_at is not None and moment - failed_at < timedelta(seconds=FAILURE_BACKOFF_S):
        return 0
    from chimera.skills.bundles import _NotFoundError

    fetch = get or _github_get
    stored = 0
    state.last_failure = None
    for repo in due(home, repos, now=moment)[:batch]:
        try:
            body = fetch(f"https://api.github.com/repos/{repo}", _MAX_RESPONSE)
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 -- offline, throttled, refused: end this refresh
            if isinstance(exc, _NotFoundError):
                state.misses[repo] = _iso(moment)
                continue
            log.info("stars refresh stopped at %s: %s", repo, exc)
            state.last_failure = _iso(moment)
            break
        count = data.get("stargazers_count") if isinstance(data, dict) else None
        if isinstance(count, int) and count >= 0:
            state.readings[repo] = StarReading(count, _iso(moment))
            state.misses.pop(repo, None)
            stored += 1
    _write(home, state)
    return stored


_LOCK = threading.Lock()
_RUNNING: set[str] = set()


def schedule_refresh(home: Path, repos: Iterable[str], *, get: Fetcher | None = None) -> bool:
    """Start one background refresh for ``home`` if something is due and none is running.

    Returns whether a thread was started. Never blocks and never raises.
    """
    wanted = list(repos)
    try:
        if not due(home, wanted):
            return False
    except Exception:  # noqa: BLE001 -- a broken cache is "nothing to do now", not a 500
        return False
    key = str(Path(home).resolve())
    with _LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)

    def _run() -> None:
        try:
            refresh(home, wanted, get=get)
        except Exception as exc:  # noqa: BLE001 -- a background read must not kill anything
            log.warning("stars refresh failed: %s", exc)
        finally:
            with _LOCK:
                _RUNNING.discard(key)

    threading.Thread(target=_run, name="catalog-stars", daemon=True).start()
    return True


def _newer(reading: str, recorded: str | None) -> bool:
    mine, theirs = _parse(reading), _parse(recorded)
    return mine is not None and (theirs is None or mine >= theirs)


def apply_stars(catalog: CatalogOut, readings: dict[str, StarReading]) -> CatalogOut:
    """Put this machine's readings on the items whose ``stars_repo`` they name, when fresher."""
    for item in catalog.items:
        reading = readings.get(item.stars_repo or "")
        if reading is not None and _newer(reading.read_at, item.stars_at):
            item.stars, item.stars_at = reading.stars, reading.read_at
    return catalog


def stars_repos(catalog: CatalogOut) -> list[str]:
    return sorted({item.stars_repo for item in catalog.items if item.stars_repo})


def with_stars(catalog: CatalogOut, home: Path, *, enabled: bool,
               get: Fetcher | None = None) -> CatalogOut:
    """The catalogue with cached readings applied, and a background refresh started if allowed."""
    with contextlib.suppress(Exception):
        apply_stars(catalog, cached_stars(home))
    if enabled:
        schedule_refresh(home, stars_repos(catalog), get=get)
    return catalog


__all__ = [
    "FAILURE_BACKOFF_S",
    "REFRESH_BATCH",
    "STARS_TTL_S",
    "StarReading",
    "apply_stars",
    "cache_path",
    "cached_stars",
    "due",
    "refresh",
    "schedule_refresh",
    "stars_repos",
    "with_stars",
]
