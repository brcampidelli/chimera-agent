"""Print the caching-aware dollar model next to the measured token sweep.

This is a MODEL (assumptions in chimera/eval/cache_cost.py), not a measurement — it needs
no API key. It answers the caveat every sweep RESULTS carries: how much does prompt caching
narrow the DOLLAR win, and when does it flip it?

Run: python bench/hierarchy_sweep/cache_cost.py   (env READ_MULT, WRITE_MULT)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def main() -> None:
    from chimera.eval.cache_cost import CacheModel, compare_under_caching, format_table

    model = CacheModel(
        read_mult=float(os.environ.get("READ_MULT", "0.1")),
        write_mult=float(os.environ.get("WRITE_MULT", "1.0")),
    )

    print("== D axis (Q = D): token win vs cached-dollar win ==")
    print(format_table([compare_under_caching(d) for d in (2, 3, 4, 5)], model=model))

    print("\n== Q axis (D = 3): the dollar win erodes and inverts as the chat lengthens ==")
    print(format_table([compare_under_caching(3, q=q, model=model) for q in (2, 3, 6, 12)], model=model))


if __name__ == "__main__":
    main()
