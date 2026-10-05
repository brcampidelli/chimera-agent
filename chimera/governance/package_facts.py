"""What PyPI says about a package, for the card that asks about installing it (study 30, S30-28).

`package_install` asks before `pip install <name>`, and the card showed the command and nothing
else. A squatted or hallucinated name looks exactly like a real one on that card; what tells them
apart is whether the index has heard of it and since when (arXiv 2606.13918 reads age and existence
as the cheap signals). This module adds that line, under `CHIMERA_SHELL_FETCH_GUARD`, and nothing
more: it never changes a verdict, and when PyPI cannot be reached it says **unknown** rather than
guessing in either direction.

One GET to `https://pypi.org/pypi/<name>/json` per package, with a short timeout and a per-process
cache. The name is the one the agent is about to install, and the request goes to the index the
install would reach anyway, so it tells PyPI nothing the install would not.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_PIP = re.compile(r"\b(?:pip3?|pipx|uv\s+pip|uv\s+tool)\s+install\b(?P<rest>[^\n;&|]*)|\buv\s+add\b(?P<uv>[^\n;&|]*)")
# Options that take the next word as their value, so it is not a package.
_VALUE_OPTS = frozenset(
    {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url",
     "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root",
     "--python", "-p", "--group", "--optional", "--extra"}
)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
TIMEOUT_S = 3.0

_cache: dict[str, str] = {}
_lock = threading.Lock()


def pypi_packages(command: str) -> list[str]:
    """The package names a pip-family command installs from the index, in order, once each.

    Not a requirements file, a path, an editable or a URL: those install what the repository or the
    user already pins, and `package_install` does not ask about them either.
    """
    names: list[str] = []
    for match in _PIP.finditer(command or ""):
        words = [w.strip("'\"") for w in (match.group("rest") or match.group("uv") or "").split()]
        skip = False
        for word in words:
            if skip:
                skip = False
                continue
            if word.startswith("-"):
                skip = word in _VALUE_OPTS
                continue
            if word.startswith((".", "/", "~", "\\")) or "://" in word or word.endswith((".whl", ".gz", ".zip")):
                continue
            name = _NAME.match(word)
            if name is not None and name.group(0).lower() not in (n.lower() for n in names):
                names.append(name.group(0))
    return names


def _pypi_json(name: str, timeout: float) -> Any:
    """PyPI's JSON for ``name``; None when PyPI says it does not exist (404). Raises when PyPI
    could not be reached — the caller turns that into ``unknown``."""
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed https host
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _first_release(data: Any) -> tuple[str, int]:
    """The date of the earliest upload, ``YYYY-MM-DD`` (or ``""``), and how many releases there are."""
    releases = data.get("releases") if isinstance(data, dict) else None
    if not isinstance(releases, dict):
        return "", 0
    times = [
        str(upload.get("upload_time_iso_8601") or upload.get("upload_time") or "")
        for files in releases.values() if isinstance(files, list)
        for upload in files if isinstance(upload, dict)
    ]
    dated = sorted(t[:10] for t in times if len(t) >= 10)
    return (dated[0] if dated else ""), len(releases)


def pypi_line(name: str, *, timeout: float = TIMEOUT_S) -> str:
    """One line for the card: exists since when, does not exist, or unknown."""
    key = name.lower()
    with _lock:
        if key in _cache:
            return _cache[key]
    try:
        data = _pypi_json(name, timeout)
    except Exception:  # noqa: BLE001 - display only: any failure to reach PyPI reads as unknown
        return f"PyPI: {name} unknown (PyPI could not be reached)"  # not cached: it may come back
    if data is None:
        line = f"PyPI: {name} does not exist on PyPI"
    else:
        first, count = _first_release(data)
        since = f", first released {first}" if first else ""
        line = f"PyPI: {name} exists{since} ({count} release{'s' if count != 1 else ''})"
    with _lock:
        _cache[key] = line
    return line


def card_lines(command: str) -> list[str]:
    """The PyPI line for every package ``command`` installs by name."""
    return [pypi_line(name) for name in pypi_packages(command)]


def clear_cache() -> None:
    """Forget every answer (tests; a long-lived process that wants a fresh look)."""
    with _lock:
        _cache.clear()
