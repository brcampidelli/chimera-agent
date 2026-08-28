"""Run the memory-graph bench: does the entity-relation layer put better facts in the prompt?

Deterministic and offline — no model, no network, no cost. The whole run is a few seconds, which is
why there is no sampling, no retry and no resume machinery here.

Design, thresholds and predictions are fixed in PREREGISTRATION.md, written before the first run.
Read the activation abort there before reading a null: a run where the graph never fired is not
evidence that firing does not help.

    python bench/memory_graph/run_graph.py

``BENCH_SEEDS=42,43,44`` overrides the seeds (three is the registered minimum — two seeds alert,
three decide). ``BENCH_OUT`` overrides where the JSON lands.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from chimera.eval.memory_graph_bench import (  # noqa: E402
    SEEDS,
    GraphBenchError,
    format_report,
    run_graph_bench,
)

_OUT = Path(os.environ.get("BENCH_OUT", str(HERE / "results")))


def _seeds() -> tuple[int, ...]:
    raw = os.environ.get("BENCH_SEEDS", "")
    if not raw.strip():
        return SEEDS
    return tuple(int(part) for part in raw.split(",") if part.strip())


def main() -> int:
    seeds = _seeds()
    if len(seeds) < 3:
        # Not a style preference. With two measurements there is no variance to estimate, only a
        # difference — and this project has already once mistaken the low end of two for instability.
        print(f"refusing to run with {len(seeds)} seed(s): two seeds alert, three decide")
        return 2

    with tempfile.TemporaryDirectory(prefix="chimera-graph-bench-") as tmp:
        try:
            report = run_graph_bench(Path(tmp), seeds=seeds)
        except GraphBenchError as exc:
            # The abort path is a result too, and it must not be mistakable for a crash.
            print(f"\nRUN ABORTED — the apparatus is not measuring what it claims:\n  {exc}")
            return 3

    print()
    print(format_report(report))

    _OUT.mkdir(parents=True, exist_ok=True)
    target = _OUT / "graph_ab.json"
    target.write_text(json.dumps(report.summary(), indent=2), encoding="utf-8")
    print(f"\nwritten: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
