"""Fit the calibration map the package ships for the governance decision, from the bench's rows.

    python bench/jev_decisions/fit_map.py [results/2026-09-19-local-L.jsonl]

Prints the :class:`chimera.decisions.calibration.PlattMap` as JSON — the constants in
`chimera/decisions/maps.py` are this output, and `tests/test_a_decision_has_a_contract.py` refits
from the same rows and holds them equal, so the shipped map cannot drift from the rows it claims
(§2z: a constant with no procedure behind it is a number nobody can check).

The rows: arm L, first repetition, no wrapper, the easy and ambiguous slices — 55 items, 24 attacks —
the same base `recalibrate.py` used leave-one-family-out, where Platt took Brier 0.268 → 0.135 and ECE
0.299 → 0.085 at the floor. The shipped map is fitted on all 55, since the held-out numbers were what
justified the method and a map fitted on more rows is the better map (RESULTS.md §7b).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.decisions.calibration import PlattMap, prompt_hash  # noqa: E402
from chimera.decisions.governance import DANGER, DECISION  # noqa: E402
from chimera.decisions.local import LocalLogprobBackend  # noqa: E402

DEFAULT_ROWS = Path(__file__).resolve().parent / "results" / "2026-09-19-local-L.jsonl"
MODEL = "qwen3:4b"


def calibration_pairs(path: Path) -> list[tuple[float, int]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    base = [
        r for r in rows
        if r.get("arm") == "L" and r.get("wrapper") is None and r.get("rep") == 0
        and r.get("slice") in ("easy", "ambiguous") and r.get("p") is not None
    ]
    return [(float(r["p"]), 1 if r["label"] == "attack" else 0) for r in base]


def fit(path: Path = DEFAULT_ROWS) -> PlattMap:
    backend = LocalLogprobBackend("http://127.0.0.1:11434", MODEL)
    digest = prompt_hash(backend.name, backend.model, backend.instrument(DANGER))
    return PlattMap.fit(
        calibration_pairs(path), decision=DECISION, backend=backend.name, model=MODEL, prompt_hash=digest,
        source="bench/jev_decisions/results/2026-09-19-local-L.jsonl — arm L, rep 0, no wrapper, easy + ambiguous",
        note=(
            "leave-one-family-out on the same rows (RESULTS.md §7b): Brier 0.268 → 0.135, ECE 0.299 → 0.085 "
            "(floor 0.08), catch 20/24 at FR 6/31 at τ = 0.5 — the hosted judge's operating point at US$ 0"
        ),
        fitted_at="2026-09-19",
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(json.dumps(fit(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROWS).to_dict(), indent=2, ensure_ascii=False))
