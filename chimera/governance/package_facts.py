"""What PyPI says about a package, for the card that asks about installing it (study 30, S30-28).

`package_install` asks before `pip install <name>`, and the card showed the command and nothing
else. A squatted or hallucinated name looks exactly like a real one on that card; what tells them
apart is whether the index has heard of it and since when (arXiv 2606.13918 reads age and existence
as the cheap signals). This module adds that line, under `CHIMERA_SHELL_FETCH_GUARD`, and nothing
more: it never changes a verdict, and when PyPI cannot be reached it says **unknown** rather than
guessing in either direction.

One GET to `https://pypi.org/pypi/<name>/json` per package, with a short timeout and a per-process
cache (an answer for good; "could not be reached" for a minute, so an offline machine does not pay
the timeout on every question).

What the lookup tells PyPI, said plainly because the first version of this docstring got it wrong
(study 30 review): the name goes to PyPI **when the question is asked, before anyone answers it** —
so a refused install still reached PyPI, and the card exists to be read before the answer. For a
public package that is what the install would have sent. For a private one it is the reconnaissance
dependency confusion needs, so the lookup is skipped, and the card says so, whenever the command or
the environment names another index: `-i`/`--index-url`/`--extra-index-url`/`--index`/
`--default-index`/`-f`/`--find-links`/`--no-index` on the command (`-i` and `-f` also with the
value glued on), or `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, `PIP_FIND_LINKS`, `UV_INDEX_URL`,
`UV_EXTRA_INDEX_URL`, `UV_INDEX`, `UV_DEFAULT_INDEX` or `UV_FIND_LINKS` in the environment or
assigned on the command line itself (`PIP_INDEX_URL=… pip install x`). **Not detected:** an index set in
`pip.conf`/`pip.ini` or in `uv.toml`/`[tool.uv.index]` — a deployment with a private index configured
that way should leave `CHIMERA_SHELL_FETCH_GUARD` off, or accept that the names of refused installs
reach PyPI. The assembly also skips the lookup where no person reads the card (`observe`, an `allow`
or `deny` approver, an unattended surface): `governance.profile.govern_step`; and a wrapper with no
approver at all (`chimera agent --guard`) never looks anything up, since only the model reads its
refusal.

The request is made by this process, not from the agent's sandbox, so `CHIMERA_SANDBOX_NETWORK` (the
container's network) does not govern it; the guard setting does.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

_PIP = re.compile(r"\b(?:pip3?|pipx|uv\s+pip|uv\s+tool)\s+install\b(?P<rest>[^\n;&|]*)|\buv\s+add\b(?P<uv>[^\n;&|]*)")
# Options that take the next word as their value, so it is not a package.
# A missing one reads its value as a package: `pip install --trusted-host pypi.internal --timeout
# 60 foo` put `pypi.internal` and `60` on the card and sent both to PyPI (study 30 review). pip's,
# then uv's (`uv pip install`, `uv add`, `uv tool install`).
_VALUE_OPTS = frozenset(
    {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url",
     "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root",
     "--python", "-p", "--group", "--optional", "--extra",
     "--trusted-host", "--timeout", "--retries", "--proxy", "--cache-dir", "--src", "--log",
     "--log-file", "--platform", "--python-version", "--implementation", "--abi", "--only-binary",
     "--no-binary", "--upgrade-strategy", "--progress-bar", "--report", "--config-settings",
     "-C", "--global-option", "--build-option", "--exists-action", "--cert", "--client-cert",
     "--keyring-provider", "--use-feature", "--use-deprecated", "--root-user-action",
     "--index", "--default-index", "--index-strategy", "--resolution", "--prerelease",
     "--exclude-newer", "--refresh-package", "--reinstall-package", "--upgrade-package", "-P",
     "--no-build-isolation-package", "--link-mode", "--directory",
     "--project", "--package", "--with", "--with-requirements", "--with-editable",
     "--config-file", "--build-constraint", "--overrides", "--constraints",
     "--marker", "--rev", "--tag", "--branch", "--script", "--bounds",
     "--color", "--python-platform", "--torch-backend", "--allow-insecure-host"}
)
# Options that name another source of packages: with one of these the name may be private, and the
# lookup would hand it to PyPI.
_INDEX_OPTS = frozenset(
    {"-i", "--index-url", "--extra-index-url", "--index", "--default-index", "-f",
     "--find-links", "--no-index"}
)
_INDEX_ENV = (
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS", "PIP_NO_INDEX",
    "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_INDEX", "UV_DEFAULT_INDEX", "UV_FIND_LINKS",
    "UV_NO_INDEX",
)
# The short index options take their value glued on as readily as after a space: `-ihttps://…`,
# `-f./wheels`. Matched by prefix, or the glued form named another index and the lookup ran anyway.
_SHORT_INDEX_OPTS = ("-i", "-f")
# An index set for this one command by an assignment in front of it, `PIP_INDEX_URL=… pip install x`
# or `env UV_INDEX_URL=… uv add x`: the same as the variable in the environment, written inline.
_INLINE_INDEX_ENV = re.compile(r"(?:^|\s)((?:PIP|UV)_[A-Z_]*(?:INDEX|FIND_LINKS)[A-Z_]*)=")
# `C:\wheels\acme` is a path, not a package called `C`.
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
TIMEOUT_S = 3.0
# How long "PyPI could not be reached" is remembered. Long enough that a run asking about ten
# installs offline pays the timeout once, short enough that a network that comes back is used.
UNKNOWN_TTL_S = 60.0

_cache: dict[str, str] = {}
_unknown_until: dict[str, float] = {}
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
            if (
                word.startswith((".", "/", "~", "\\")) or _WINDOWS_PATH.match(word) or "://" in word
                or word.endswith((".whl", ".gz", ".zip"))
            ):
                continue
            name = _NAME.match(word)
            if name is not None and name.group(0).lower() not in (n.lower() for n in names):
                names.append(name.group(0))
    return names


def other_index(command: str, environ: Mapping[str, str] | None = None) -> str:
    """What names another package index for ``command``, or ``""`` when only PyPI is in play."""
    for match in _PIP.finditer(command or ""):
        for word in (match.group("rest") or match.group("uv") or "").split():
            option = word.strip("'\"").split("=", 1)[0]
            if option in _INDEX_OPTS:
                return option
            if option.startswith(_SHORT_INDEX_OPTS) and not option.startswith("--"):
                return option[:2]
    if (inline := _INLINE_INDEX_ENV.search(command or "")) is not None:
        return inline.group(1)
    env = os.environ if environ is None else environ
    for name in _INDEX_ENV:
        if (env.get(name) or "").strip():
            return name
    return ""


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
    unknown = f"PyPI: {name} unknown (PyPI could not be reached)"
    with _lock:
        if key in _cache:
            return _cache[key]
        if _unknown_until.get(key, 0.0) > time.monotonic():
            return unknown
    try:
        data = _pypi_json(name, timeout)
    except Exception:  # noqa: BLE001 - display only: any failure to reach PyPI reads as unknown
        with _lock:  # remembered briefly, not for good: the network may come back
            _unknown_until[key] = time.monotonic() + UNKNOWN_TTL_S
        return unknown
    if data is None:
        line = f"PyPI: {name} does not exist on PyPI"
    else:
        first, count = _first_release(data)
        since = f", first released {first}" if first else ""
        line = f"PyPI: {name} exists{since} ({count} release{'s' if count != 1 else ''})"
    with _lock:
        _cache[key] = line
    return line


def card_lines(command: str, environ: Mapping[str, str] | None = None) -> list[str]:
    """The PyPI line for every package ``command`` installs by name — or one line saying why none
    was looked up, when another index is in play (see the module docstring)."""
    names = pypi_packages(command)
    if not names:
        return []
    source = other_index(command, environ)
    if source:
        return [f"PyPI: not checked — {source} names another index, and a private package's name "
                f"must not be sent to PyPI"]
    return [pypi_line(name) for name in names]


def clear_cache() -> None:
    """Forget every answer (tests; a long-lived process that wants a fresh look)."""
    with _lock:
        _cache.clear()
        _unknown_until.clear()
