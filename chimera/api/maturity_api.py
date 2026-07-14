"""Maturity scorecard for the desktop app: the agent's self-eval coverage tally, honest by construction.

:func:`maturity_report` resolves the scorecard three ways, in order, and always says which one it used:

- **live** — when a real repo ``tests/`` dir is reachable (running from a source checkout / CI), it
  globs the test files and computes the scorecard fresh. Cheap + keyless: a pure filesystem glob, no
  LLM, no network.
- **snapshot** — else it loads the shipped ``chimera/_maturity_snapshot.json`` (written at release time
  by ``python -m chimera.eval.maturity_snapshot``). The ``tests/`` dir is NOT packaged in the pip wheel,
  so a pip-installed app has no live evidence to glob; the snapshot is the honest stand-in and carries
  its own ``generated_for`` version so the UI can date it.
- **unavailable** — else (no live dir and no readable snapshot) it returns a zeroed, ``available=False``
  payload. Never a fabricated all-Alpha 0/N scorecard.

HONESTY: a "proven" coverage-ID means a test file with that stem EXISTS, not that it passes — this is
coverage/evidence presence, a drift flag, not a correctness claim. No benchmark performance numbers are
computed or returned here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.eval.maturity_snapshot import build_snapshot, snapshot_path


def _default_tests_dirs() -> list[Path]:
    """Candidate live ``tests/`` dirs: the repo root (from the installed package) and the cwd."""
    return [
        Path(__file__).resolve().parents[2] / "tests",  # repo root, when running from a checkout
        Path.cwd() / "tests",  # a `chimera app` launched at the repo root
    ]


def _first_live_tests_dir(candidates: list[Path]) -> Path | None:
    """The first candidate that exists and holds at least one ``test_*.py`` (real evidence to glob)."""
    for d in candidates:
        try:
            if d.is_dir() and any(d.glob("test_*.py")):
                return d
        except OSError:
            continue
    return None


def _unavailable(reason: str) -> dict[str, Any]:
    """A zeroed, honest ``available=False`` payload — never a fabricated all-Alpha scorecard."""
    return {
        "available": False,
        "source": None,
        "proven": 0,
        "total": 0,
        "ratio": 0.0,
        "level": "Alpha",
        "surfaces": [],
        "weakest": None,
        "generated_for": None,
        "note": reason,
    }


def maturity_report(
    *,
    tests_dirs: list[Path] | None = None,
    snapshot_file: Path | None = None,
) -> dict[str, Any]:
    """The maturity scorecard as a plain dict, resolved live → snapshot → unavailable.

    ``tests_dirs`` / ``snapshot_file`` default to the real resolution and exist only so tests can point
    the loader at a temp dir. Any file/parse failure falls through to the next mode (never raises), so a
    broken snapshot degrades to ``unavailable`` rather than a 500.
    """
    candidates = tests_dirs if tests_dirs is not None else _default_tests_dirs()
    live = _first_live_tests_dir(candidates)
    if live is not None:
        try:
            # Live and snapshot are identically shaped: both go through build_snapshot.
            return {"available": True, "source": "live", **build_snapshot(live)}
        except Exception:  # noqa: BLE001 — a live-glob failure falls through to the snapshot
            pass

    path = snapshot_file if snapshot_file is not None else snapshot_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("snapshot is not an object")
        return {"available": True, "source": "snapshot", **data}
    except Exception:  # noqa: BLE001 — missing/corrupt snapshot is honest "unavailable", not a 500
        return _unavailable("no test suite and no shipped snapshot were available")


__all__ = ["maturity_report"]
