"""Fetch a pinned catalogue file, verify it, cache it, and read a whole catalogue — offline too.

The rules, each one a test in ``tests/test_pinned_catalogs.py``:

* **Only pinned tuples are fetched.** :func:`fetch_pinned` takes ``(owner, repo, sha, path)`` and
  refuses any tuple that is not exactly one the pin file names. The URL is built here, from that
  tuple, as ``https://raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>`` — never from catalogue
  content and never from the caller.
* **Verified before parsed.** The sha256 of the bytes is compared with the pin before anything reads
  them. A mismatch is a refusal: the bytes are neither parsed nor cached.
* **Bounded.** One file is capped at :data:`~chimera.catalogs.pins.MAX_CATALOG_FILE_BYTES`; past it
  the response is refused, not truncated. The network goes through the plugin installer's fetch
  (:func:`chimera.skills.bundles._get`): HTTPS only, the two GitHub hosts only, a timeout, bounded
  retries, ``GITHUB_TOKEN`` when the environment has one.
* **Cached by commit.** Verified bytes are kept under ``CHIMERA_HOME/catalogs/cache/<owner>__<repo>/
  <sha>/<sha256>``. A pinned commit never changes, so a cached file never goes stale — it is re-hashed
  on every read anyway, and a file that no longer matches is deleted and fetched again.
* **Offline is a state, not an error.** With the files cached, the catalogue reads without a network.
  With nothing cached and no network, :func:`load_catalog` answers an empty catalogue with the reason
  in a sentence. No exception reaches the route.

Nothing here runs on its own: a catalogue is read when the screen asks for it, at the pinned commit,
and the pin only changes when a release ships a new one.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from chimera.catalogs.model import CatalogOut
from chimera.catalogs.pins import (
    MAX_CATALOG_FILE_BYTES,
    CatalogKind,
    CatalogPin,
    PinFile,
    load_pins,
    pinned_digest,
)
from chimera.telemetry import get_logger

log = get_logger(__name__)

#: ``(url, limit) -> bytes``. Raises on any failure to read; returns at most ``limit`` bytes or raises.
Fetcher = Callable[[str, int], bytes]


class CatalogRefused(Exception):
    """A file that will not be read: not pinned, larger than the cap, or not the pinned bytes."""


class CatalogUnreachable(Exception):
    """A pinned file that is not cached and could not be fetched. The catalogue reads as offline."""


def _github_get(url: str, limit: int) -> bytes:
    from chimera.skills import bundles

    return bundles._get(url, accept="*/*", limit=limit)


def cache_root(home: Path) -> Path:
    return Path(home) / "catalogs" / "cache"


def _cache_path(home: Path, owner: str, repo: str, sha: str, digest: str) -> Path:
    # Every segment was validated by the pin model (names, a 40-hex sha, a 64-hex sha256), so none
    # of them can climb out of the cache folder.
    return cache_root(home) / f"{owner}__{repo}" / sha / digest


def _read_cached(path: Path, digest: str) -> bytes | None:
    try:
        if path.stat().st_size > MAX_CATALOG_FILE_BYTES:
            raise ValueError("oversized")
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        data = b""
    if data and hashlib.sha256(data).hexdigest() == digest:
        return data
    # Present but wrong — a partial write from another tool, a disk error, a hand edit. Never served;
    # dropped so the next read fetches the pinned bytes again.
    with contextlib.suppress(OSError):
        path.unlink()
    return None


def _write_cached(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        # A read-only home still shows the catalogue; it just fetches again next time.
        log.warning("could not cache a catalogue file at %s: %s", path, exc)


def fetch_pinned(
    owner: str,
    repo: str,
    sha: str,
    path: str,
    *,
    home: Path,
    pins: PinFile | None = None,
    get: Fetcher | None = None,
    network: bool = True,
) -> bytes:
    """The pinned bytes of one catalogue file: from the cache, or fetched and verified.

    Raises :class:`CatalogRefused` for a tuple that is not pinned, an oversized response or a sha256
    mismatch, and :class:`CatalogUnreachable` when the file is not cached and cannot be fetched
    (``network=False`` reads the cache only).
    """
    digest = pinned_digest(pins or load_pins(), owner, repo, sha, path)
    if digest is None:
        raise CatalogRefused(f"{owner}/{repo}@{sha[:12]}:{path} is not a pinned catalogue file")
    cached_at = _cache_path(home, owner, repo, sha, digest)
    cached = _read_cached(cached_at, digest)
    if cached is not None:
        return cached
    if not network:
        raise CatalogUnreachable("not cached, and the source was not reachable")
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{urllib.parse.quote(path)}"
    try:
        body = (get or _github_get)(url, MAX_CATALOG_FILE_BYTES)
    except Exception as exc:  # noqa: BLE001 -- any failure to read is "unreachable", never a crash
        message = str(exc)
        if "larger than" in message:
            raise CatalogRefused(f"{path} is larger than the catalogue limit") from exc
        raise CatalogUnreachable(message or type(exc).__name__) from exc
    if len(body) > MAX_CATALOG_FILE_BYTES:
        raise CatalogRefused(
            f"{path} is larger than the {MAX_CATALOG_FILE_BYTES // 1024} KB catalogue limit"
        )
    actual = hashlib.sha256(body).hexdigest()
    if actual != digest:
        # Not parsed, not cached. A commit sha names fixed content, so a different hash means the
        # response is not what the release pinned — whatever the reason, it is not read.
        raise CatalogRefused(
            f"{path}: sha256 {actual[:12]}… is not the pinned {digest[:12]}… — refused, not read"
        )
    _write_cached(cached_at, body)
    return body


def _read_files(
    pin: CatalogPin, home: Path, pins: PinFile, get: Fetcher | None
) -> tuple[dict[str, bytes], list[str], str | None]:
    files: dict[str, bytes] = {}
    problems: list[str] = []
    offline: str | None = None
    for path in pin.files:
        try:
            files[path] = fetch_pinned(
                pin.owner, pin.repo, pin.sha, path,
                home=home, pins=pins, get=get, network=offline is None,
            )
        except CatalogRefused as exc:
            problems.append(str(exc))
        except CatalogUnreachable as exc:
            # The first failure decides the rest: once the source did not answer, every other file
            # comes from the cache or not at all, so a dead link costs one timeout, not sixty.
            if offline is None:
                offline = str(exc)
            problems.append(f"{path}: not cached, and the source could not be reached")
    return files, problems, offline


def load_catalog(
    kind: CatalogKind, home: Path, *, pins: PinFile | None = None, get: Fetcher | None = None
) -> CatalogOut:
    """One catalogue, read from its pin. Never raises: what could not be read is in ``reason``."""
    from chimera.catalogs.parse import parse_catalog

    try:
        pin_file = pins or load_pins()
    except (OSError, ValueError) as exc:
        log.warning("the shipped catalogue pin file does not validate: %s", exc)
        return CatalogOut(kind=kind, repo="", sha="", available=False,
                          reason="the catalogue pin file shipped with this Chimera is unreadable")
    pin = pin_file.catalogs.get(kind)
    if pin is None:
        return CatalogOut(kind=kind, repo="", sha="", available=False,
                          reason=f"this Chimera pins no {kind} catalogue")
    files, problems, offline = _read_files(pin, Path(home), pin_file, get)
    items, skipped = parse_catalog(kind, pin, files)
    problems.extend(skipped)
    repo = f"{pin.owner}/{pin.repo}"
    if not files:
        if offline is not None:
            reason = (f"offline, and the {kind} catalogue has not been downloaded yet "
                      f"({offline}); it appears once {repo} is reachable")
        else:
            reason = f"every {kind} catalogue file was refused; nothing was read"
    elif not items:
        reason = f"nothing in the {kind} catalogue could be read"
    elif len(files) < len(pin.files):
        missing = len(pin.files) - len(files)
        reason = (f"partial: {missing} of {len(pin.files)} catalogue files could not be read"
                  + (" (offline)" if offline is not None else ""))
    else:
        reason = None
    return CatalogOut(kind=kind, repo=repo, sha=pin.sha, items=items, available=bool(items),
                      reason=reason, problems=problems, committed_at=pin.committed_at)


__all__ = [
    "CatalogRefused",
    "CatalogUnreachable",
    "Fetcher",
    "cache_root",
    "fetch_pinned",
    "load_catalog",
]
