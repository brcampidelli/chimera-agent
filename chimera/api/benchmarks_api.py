"""Benchmark report for the desktop app: the agent's REAL, recorded performance numbers, honestly framed.

:func:`benchmark_report` loads the shipped ``chimera/_benchmark_snapshot.json`` (written at release time
by ``python -m chimera.eval.benchmark_snapshot``). Unlike the maturity scorecard there is no "live" mode
— the ``bench/`` result dirs aren't packaged in the wheel, so the committed snapshot IS the data. A
missing or malformed snapshot degrades to an honest ``available=False`` payload — never a 500.

Both blocks carry their ``n`` / ``ci`` / ``significant`` fields so every consumer shows the caveat
alongside the number:

- **internal_lift** — the weak-model lift (9% → 15%, +6pp at n=100; significant, CI excludes 0).
- **external** — the humbling Terminal-Bench number (scaffold didn't lift a competent model, not
  significant at N=40). Published alongside the internal one on purpose — that pairing is the integrity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.eval.benchmark_snapshot import snapshot_path


def _unavailable() -> dict[str, Any]:
    """The honest empty payload when no snapshot is readable — never a fabricated benchmark."""
    return {"available": False, "internal_lift": None, "external": [], "generated_for": None}


def benchmark_report(*, snapshot_file: Path | None = None) -> dict[str, Any]:
    """The benchmark snapshot as a plain dict, loaded from the shipped JSON (or unavailable).

    ``snapshot_file`` defaults to the real shipped location and exists only so tests can point the
    loader at a temp file. Any file/parse failure (missing, malformed, not an object) returns the
    ``available=False`` payload rather than raising, so a broken snapshot is honest, not a 500.
    """
    path = snapshot_file if snapshot_file is not None else snapshot_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "internal_lift" not in data:
            raise ValueError("snapshot is not a benchmark object")
        return {
            "available": True,
            "internal_lift": data.get("internal_lift"),
            "external": data.get("external", []),
            "generated_for": data.get("generated_for"),
        }
    except Exception:  # noqa: BLE001 — missing/corrupt snapshot is honest "unavailable", not a 500
        return _unavailable()


__all__ = ["benchmark_report"]
